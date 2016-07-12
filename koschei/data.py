# Copyright (C) 2016  Red Hat, Inc.
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

#import wtforms

from sqlalchemy import insert

from koschei.models import (Package, BasePackage, PackageGroup,
                            PackageGroupRelation, GroupACL, User,
                            get_or_create)


class PackagesDontExist(Exception):
    def __init__(self, packages):
        super(PackagesDontExist, self).__init__(self)
        self.packages = packages

    def __str__(self):
        return "Packages don't exist: " + ','.join(self.packages)


def track_packages(db, collection, package_names):
    """
    Sets given packages as tracked within collection.

    :param: collection Collection ORM object where packages should be added
    :param: package_names list of package names to track
    :raises: PackagesDontExist if some of the packages cannot be found
    :return: a list of Package objects that were newly tracked
    """
    existing = db.query(Package)\
        .filter(Package.name.in_(package_names))\
        .filter(Package.collection_id == collection.id)
    nonexistent = package_names - {p.name for p in existing}
    if nonexistent:
        raise PackagesDontExist(nonexistent)
    to_add = [p for p in existing if not p.tracked]
    db.query(Package)\
        .filter(Package.id.in_(p.id for p in to_add))\
        .update({'tracked': True})
    return to_add


def set_group_content(db, group, packages, append=False):
    """
    Makes given group contain given packages (by name).
    In append mode (append=True) doesn't remove any packages from the group.
    With append=False, makes the group contain only specified packages.

    :param: db database session
    :param: group PackageGroup object
    :param: packages list of package names to be in given group
    :param: append whether to clear the group first or append to existing content
    """
    contents = set(packages)
    base_ids = db.query(BasePackage.id)\
        .filter(BasePackage.name.in_(contents))\
        .all_flat(set)
    if len(base_ids) != len(contents):
        raise RuntimeError("Some packages weren't found")
    if append:
        base_ids -= db.query(PackageGroupRelation.base_id)\
            .filter(PackageGroup.id == group.id)\
            .filter(PackageGroupRelation.base_id.in_(base_ids))\
            .all_flat(set)
    rels = [dict(group_id=group.id, base_id=base_id) for base_id in base_ids]
    if not append:
        db.query(PackageGroupRelation).filter_by(group_id=group.id).delete()
    if rels:
        db.execute(insert(PackageGroupRelation, rels))


def set_group_maintainers(db, group, maintainers):
    """
    Sets given group maintaners to given list of names.

    :param: db database session
    :param: group PackageGroup object
    :param: maintainers list of maintainer names
    """
    users = [get_or_create(db, User, name=name) for name in set(maintainers)]
    db.query(GroupACL)\
        .filter(GroupACL.group_id == group.id)\
        .delete()
    acls = [dict(group_id=group.id, user_id=user.id)
            for user in users]
    db.execute(insert(GroupACL), acls)
