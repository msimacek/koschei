# Copyright (C) 2014  Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Author: Michael Simacek <msimacek@redhat.com>

import koji

from datetime import datetime
from sqlalchemy.exc import IntegrityError

from koschei import util
from koschei.models import (Build, DependencyChange, KojiTask, Package,
                            PackageGroup, PackageGroupRelation)
from koschei.plugin import dispatch_event


class PackagesDontExist(Exception):
    def __init__(self, names, *args, **kwargs):
        super(PackagesDontExist, self).__init__(*args, **kwargs)
        self.names = names


def check_package_state(package, prev_state):
    new_state = package.msg_state_string
    if prev_state != new_state:
        dispatch_event('package_state_change', package=package,
                       prev_state=prev_state,
                       new_state=new_state)


class Backend(object):

    def __init__(self, log, db, koji_session, srpm_cache=None):
        self.log = log
        self.db = db
        self.koji_session = koji_session
        self.srpm_cache = srpm_cache

    def submit_build(self, package):
        build = Build(package_id=package.id, state=Build.RUNNING)
        name = package.name
        build.state = Build.RUNNING
        build_opts = {}
        if package.arch_override:
            build_opts = {'arch_override': package.arch_override}
        srpm, srpm_url = (util.get_last_srpm(self.koji_session, name)
                          or (None, None))
        if srpm_url:
            package.manual_priority = 0
            build.task_id = util.koji_scratch_build(self.koji_session, name,
                                                    srpm_url, build_opts)
            build.started = datetime.now()
            build.epoch = srpm['epoch']
            build.version = srpm['version']
            build.release = srpm['release']
            self.db.add(build)
            self.db.flush()
            self.flush_depchanges(build)
        else:
            package.ignored = True

    def flush_depchanges(self, build):
        self.db.query(DependencyChange)\
               .filter_by(package_id=build.package_id)\
               .filter_by(applied_in_id=None)\
               .delete()

    def get_newer_build_if_exists(self, package):
        [info] = self.koji_session.listTagged(util.source_tag,
                                              latest=True,
                                              package=package.name,
                                              inherit=True) or [None]
        if self.is_build_newer(package.last_build, info):
            return info

    def is_build_newer(self, current_build, task_info):
        return (task_info and (not current_build or
                               util.compare_evr((current_build.epoch,
                                                 current_build.version,
                                                 current_build.release),
                                                (task_info['epoch'],
                                                 task_info['version'],
                                                 task_info['release'])) < 0)
                and self.db.query(Build)
                .filter_by(task_id=task_info['task_id'])
                .count() == 0)

    def register_real_build(self, package, build_info):
        # TODO send fedmsg for real builds?
        try:
            state_map = {koji.BUILD_STATES['COMPLETE']: Build.COMPLETE,
                         koji.BUILD_STATES['FAILED']: Build.FAILED}
            build = Build(task_id=build_info['task_id'], real=True,
                          version=build_info['version'], epoch=build_info['epoch'],
                          release=build_info['release'], package_id=package.id,
                          state=state_map[build_info['state']])
            self.db.add(build)
            self.db.flush()
            self._build_completed(build)
            self.flush_depchanges(build)
            self.log.info('Registering real build for {}, task_id {}.'
                          .format(package, build.task_id))
            return build
        except IntegrityError:
            # other daemon adds the same concurrently
            self.db.rollback()

    def update_build_state(self, build, state):
        if state in Build.KOJI_STATE_MAP:
            state = Build.KOJI_STATE_MAP[state]
            build_id = build.id
            self.db.expire_all()
            # lock build
            build = self.db.query(Build).filter_by(id=build_id)\
                           .with_lockmode('update').first()
            if not build or build.state == state:
                # other process did the job already
                self.db.rollback()
                return
            if state == Build.CANCELED:
                self.log.info('Deleting build {0} because it was canceled'
                              .format(build))
                self.db.delete(build)
                self.db.commit()
                return
            assert state in (Build.COMPLETE, Build.FAILED)
            self.log.info('Setting build {build} state to {state}'
                          .format(build=build,
                                  state=Build.REV_STATE_MAP[state]))
            self._build_completed(build)
            self.db.expire(build.package)
            # lock package so there are no concurrent state changes
            package = self.db.query(Package).filter_by(id=build.package_id)\
                             .with_lockmode('update').one()
            prev_state = package.msg_state_string
            build.state = state
            # unlock
            self.db.commit()
            new_state = package.msg_state_string
            if prev_state != new_state:
                dispatch_event('package_state_change', package=package,
                               prev_state=prev_state,
                               new_state=new_state)

    def _build_completed(self, build):
        task_info = self.koji_session.getTaskInfo(build.task_id)
        if task_info['create_time']:
            build.started = util.parse_koji_time(task_info['create_time'])
        if task_info['completion_time']:
            build.finished = util.parse_koji_time(task_info['completion_time'])
        else:
            # When fedmsg delivery is fast, the time is not set yet
            build.finished = datetime.now()
        subtasks = self.koji_session.getTaskChildren(build.task_id,
                                                     request=True)
        build_arch_tasks = [task for task in subtasks
                            if task['method'] == 'buildArch']
        for task in build_arch_tasks:
            try:
                # They all have the same repo_id, right?
                build.repo_id = task['request'][4]['repo_id']
            except KeyError:
                pass
            db_task = KojiTask(build_id=build.id,
                               task_id=task['id'],
                               state=task['state'],
                               started=util.parse_koji_time(task['create_time']),
                               finished=util.parse_koji_time(task['completion_time']),
                               arch=task['arch'])
            self.db.add(db_task)
        self.db.flush()

    def add_group(self, group, pkgs):
        group_obj = self.db.query(PackageGroup)\
                           .filter_by(name=group).first()
        if not group_obj:
            group_obj = PackageGroup(name=group)
            self.db.add(group_obj)
            self.db.flush()
        for pkg in pkgs:
            rel = PackageGroupRelation(group_id=group_obj.id,
                                       package_id=pkg.id)
            self.db.add(rel)

    def register_real_builds(self, task_info_mapping):
        """
        Takes a dictionary mapping package (ORM entity) to koji task_infos
        and registers possible real builds.
        """
        for pkg, info in task_info_mapping.items():
            if self.is_build_newer(pkg.last_build, info):
                self.register_real_build(pkg, info)
                self.db.commit()

    def refresh_latest_builds(self, packages):
        """
        Checks Koji for latest builds of all packages and registers possible
        new real builds and obtains srpms for them
        """
        source_tag = util.koji_config['source_tag']
        infos = util.itercall(self.koji_session, packages,
                              lambda k, p: k.listTagged(source_tag, latest=True,
                                                        package=p.name, inherit=True))
        task_infos = {pkg: i[0] for pkg, i in zip(packages, infos) if i}
        blocked = [p['package_name'] for p in
                   self.koji_session.listPackages(tagID=source_tag)
                   if p['blocked']]
        if blocked:
            self.db.query(Package).filter(Package.name.in_(blocked))\
                   .update({'ignored': True}, synchronize_session=False)
            self.db.commit()
        self.register_real_builds(task_infos)
        self.srpm_cache.get_latest_srpms([i for (_, i) in task_infos.items()])

    def add_packages(self, names, group=None, static_priority=None,
                     manual_priority=None):
        newly_added_prio = util.config['priorities']['newly_added']
        existing = self.db.query(Package).filter(Package.name.in_(names))
        existing_names = [x.name for x in existing]
        koji_pkgs = util.get_koji_packages(names)
        nonexistent = [name for name, pkg in zip(names, koji_pkgs) if not pkg]
        if nonexistent:
            raise PackagesDontExist(nonexistent)
        pkgs = []
        for name in names:
            if name not in existing_names:
                pkg = Package(name=name)
                pkg.static_priority = static_priority or 0
                pkg.manual_priority = manual_priority or newly_added_prio
                self.db.add(pkg)
                pkgs.append(pkg)
        for pkg in existing:
            if pkg.ignored:
                pkg.ignored = False
                pkgs.append(pkg)
        self.db.flush()
        if group:
            self.add_group(group, pkgs)
        return pkgs
