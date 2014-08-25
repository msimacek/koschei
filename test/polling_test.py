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

from mock import Mock, patch, call
from common import DBTest

from koschei import models as m
from koschei.polling import Polling

class PollingTest(DBTest):
    def prepare_builds(self, **kwargs):
        builds = {}
        for i, (name, state) in enumerate(sorted(kwargs.items())):
            pkg = m.Package(name=name)
            self.s.add(pkg)
            self.s.flush()
            build = m.Build(package_id=pkg.id, task_id=i + 1, state=state)
            self.s.add(build)
            builds[name] = build
        self.s.commit()
        return builds

    def get_koji_mock(self, state='CLOSED'):
        koji_mock = Mock()
        koji_mock.getTaskInfo = Mock(return_value={'state': koji.TASK_STATES[state]})
        return koji_mock

    def test_poll_none(self):
        self.prepare_builds(rnv=m.Build.COMPLETE, eclipse=m.Build.FAILED)
        with patch('koschei.polling.update_build_state') as update_mock:
            koji_mock = self.get_koji_mock()
            polling = Polling(db_session=self.s, koji_session=koji_mock)
            polling.main()
            self.assertFalse(koji_mock.getTaskInfo.called)
            self.assertFalse(update_mock.called)

    def test_poll_complete(self):
        builds = self.prepare_builds(rnv=m.Build.RUNNING)
        with patch('koschei.polling.update_build_state') as update_mock:
            koji_mock = self.get_koji_mock()
            polling = Polling(db_session=self.s, koji_session=koji_mock)
            polling.main()
            update_mock.assert_called_once_with(self.s, builds['rnv'], 'CLOSED')

    def test_poll_multiple(self):
        builds = self.prepare_builds(rnv=m.Build.RUNNING, eclipse=m.Build.RUNNING,
                                     expat=m.Build.FAILED)
        with patch('koschei.polling.update_build_state') as update_mock:
            koji_mock = self.get_koji_mock(state='FAILED')
            polling = Polling(db_session=self.s, koji_session=koji_mock)
            polling.main()
            update_mock.assert_has_calls([call(self.s, builds['rnv'], 'FAILED'),
                                          call(self.s, builds['eclipse'], 'FAILED')],
                                         any_order=True)