import argparse
import datetime
import json
import logging
import sys
from pathlib import Path
from typing import Optional, Dict, cast

import cherrypy
from bson import ObjectId
from mongoengine import Document, StringField, DateTimeField, connect, DictField, BooleanField, \
    IntField


logger = logging.getLogger(__name__)


CODE_DB_VERSION = 1


def upgrade_0_to_1() -> None:
    pass

DB_UPGRADES = {
    0: upgrade_0_to_1
}

class Version(Document):
    version = IntField(required=True)


class Site(Document):
    site_id = StringField(required=True, unique=True)
    key = StringField(required=True)
    last_seen = DateTimeField(required=True)
    crt_maintainer_email = StringField()


class Test(Document):
    site_id = StringField(required=True)
    test_start_time = DateTimeField(required=True)
    test_end_time = DateTimeField(required=True)
    stdout = StringField()
    stderr = StringField()
    log = StringField()
    module = StringField()
    cls = StringField()
    function = StringField()
    test_name = StringField()
    results = DictField()
    run_id = StringField(required=True)
    branch = StringField(required=True)
    extras = DictField()


class RunEnv(Document):
    run_id = StringField(required=True)
    site_id = StringField(required=True)
    env = DictField()
    config = DictField()
    run_start_time = DateTimeField(required=True)
    branch = StringField(required=True)
    run_end_time = DateTimeField()
    failed_count = IntField(default=0)
    completed_count = IntField(default=0)


def strtime(d):
    return d.strftime('%a, %b %d, %Y - %H:%M')


class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime.datetime):
            return obj.strftime('%a, %b %d, %Y - %H:%M:%S')
        if isinstance(obj, ObjectId):
            return str(obj)
        return super().default(obj)

    def iterencode(self, value):
        for chunk in super().iterencode(value):
            yield chunk.encode("utf-8")


class TestingAggregatorApp(object):
    def __init__(self):
        self.seq = 0

    @cherrypy.expose
    def index(self) -> None:
        raise cherrypy.HTTPRedirect('/summary.html')

    @cherrypy.expose
    @cherrypy.tools.json_in()
    def result(self) -> None:
        json = cherrypy.request.json
        if not 'id' in json:
            raise cherrypy.HTTPError(400, 'Missing id')
        if not 'key' in json:
            raise cherrypy.HTTPError(400, 'Missing key')
        site_id = json['id']
        key = json['key']
        data = json['data']

        site = self._check_authorized(site_id, key)
        if not site:
            raise cherrypy.HTTPError(403, 'This ID is associated with another key')

        self.seq += 1
        self._save_test(site_id, data)

        module = data['module']
        function = data['function']
        if module == '_conftest':
            if function == '_discover_environment':
                self._save_environment(site, data)
            if function == '_end':
                self._end_tests(site_id, data)

        self._update_totals(site_id, data)

    def _update_totals(self, site_id, data: Dict[str, object]) -> None:
        run_id = data['run_id']
        branch = data['branch']

        results = data['results']
        failed = False
        for k, v in results.items():
            if not v['passed']:
                failed = True
        if failed:
            RunEnv.objects(site_id=site_id, run_id=run_id, branch=branch).update(inc__failed_count=1)
        else:
            RunEnv.objects(site_id=site_id, run_id=run_id, branch=branch).update(inc__completed_count=1)

    def _end_tests(self, site_id: str, data: Dict[str, object]) -> None:
        run_id = data['run_id']
        branch = data['branch']
        time = data['test_end_time']
        RunEnv.objects(site_id=site_id, run_id=run_id, branch=branch).update(run_end_time=time)

    def _save_test(self, site_id: str, data: Dict[str, object]) -> None:
        data['site_id'] = site_id
        Test(**data).save()

    def _save_environment(self, site: Site, data: Dict[str, object]) -> None:
        env = cast(Dict[str, object], data['extras'])
        config = cast(Dict[str, object], env['config'])
        del env['config']
        maintainer_email = config['maintainer_email']
        if maintainer_email:
            site.crt_maintainer_email = maintainer_email
            site.save()
        run_env = RunEnv(run_id=data['run_id'], site_id=site.site_id, config=config, env=env,
                         run_start_time=env['start_time'], branch=env['git_branch'])
        run_env.save()

    def _check_authorized(self, id: str, key: str) -> Optional[Site]:
        entries = Site.objects(site_id=id)
        entry = entries.first()
        if entry:
            if key == entry.key:
                return self._update(entry)
            else:
                now = datetime.datetime.utcnow()
                diff = now - entry.last_seen
                if diff >= datetime.timedelta(days=7):
                    return self._update(entry)
                else:
                    return None
        else:
            # nothing yet
            return self._update(Site(site_id=id, key=key))

    def _update(self, entry: Site) -> Site:
        entry.last_seen = datetime.datetime.utcnow()
        print(entry.site_id)
        entry.save()
        return entry

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def summary(self) -> object:
        # get latest batch of tests on each site and return totals for passed/failed
        resp = []
        for site in Site.objects().order_by('site_id'):
            # find last run for site
            env = RunEnv.objects(site_id=site.site_id).order_by('-run_start_time')[0]
            run_id = env.run_id
            # now find all envs/branches with this run id
            envs = RunEnv.objects(site_id=site.site_id, run_id=run_id)

            branches = []
            site_data = {
                'site_id': site.site_id,
                'run_id': run_id,
                'branches': branches,
            }
            site_completed_count = 0
            site_failed_count = 0
            resp.append(site_data)
            any_running = False
            for env in envs:
                running = env.run_end_time is None
                any_running = any_running or running
                branch_data = {
                    'name': env.branch,
                    'completed_count': env.completed_count,
                    'failed_count': env.failed_count,
                    'running': running
                }
                branches.append(branch_data)
                site_completed_count += env.completed_count
                site_failed_count += env.failed_count
            site_data['running'] = any_running
            site_data['completed_count'] = site_completed_count
            site_data['failed_count'] = site_failed_count

        return resp

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def site(self, site_id) -> object:
        s = Site.objects(site_id=site_id).first()
        resp = {}
        resp['site_id'] = site_id
        test_runs = []
        resp['test_runs'] = test_runs

        runs = RunEnv.objects(site_id=site_id).order_by('-run_start_time', '+branch')[:100]

        seen = {}
        for run in runs:
            if run.run_id in seen:
                run_set = seen[run.run_id]
                if run_set['start_time'] > run.run_start_time:
                    run_set['start_time'] = run.run_start_time
            else:
                run_set = {'start_time': run.run_start_time, 'run_id': run.run_id,
                           'branches': []}
                seen[run.run_id] = run_set
                test_runs.append(run_set)
            branches = run_set['branches']
            branches.append({
                'name': run.branch,
                'failed_count': run.failed_count,
                'completed_count': run.completed_count
            })

        return resp

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def run(self, site_id, run_id) -> object:
        s = Site.objects(site_id=site_id).first()
        resp = {}
        resp['site_id'] = site_id
        resp['run_id'] = run_id
        branches = []
        resp['branches'] = branches

        runs = RunEnv.objects(site_id=site_id, run_id=run_id).order_by('+branch')

        for run in runs:
            test_list = []
            branch = run.to_mongo().to_dict()
            branch['tests'] = test_list
            branch['name'] = run.branch
            branches.append(branch)

            tests = Test.objects(site_id=site_id, run_id=run_id,
                                 branch=run.branch).order_by('+test_start_time')

            for test in tests:
                test_dict = test.to_mongo().to_dict()
                del test_dict['_id']
                test_list.append(test_dict)

        return resp

class Server:
    def __init__(self, port: int = 9909) -> None:
        self.port = port

    def start(self) -> None:
        print('webpath: %s' % (Path().absolute() / 'web'))

        json_encoder = CustomJSONEncoder()

        def json_handler(*args, **kwargs):
            value = cherrypy.serving.request._json_inner_handler(*args, **kwargs)
            return json_encoder.iterencode(value)

        cherrypy.config.update({'server.socket_port': self.port})
        cherrypy.quickstart(TestingAggregatorApp(), '/', {
            '/': {
                'tools.staticdir.root': str(Path(__file__).parent.parent.absolute() / 'web'),
                'tools.staticdir.on': True,
                'tools.staticdir.dir': '',
                'tools.json_out.handler': json_handler
            }
        })


def upgrade_db(v: Version) -> Version:
    if v.version in DB_UPGRADES:
        logger.info('Upgrading DB from %s to %s' % (v.version, v.version + 1))
        DB_UPGRADES[v.version]()
        v.update(inc__version=1)
        v.reload()
    return v


def check_db() -> None:
    connect(db='psi-j-testing-aggregator')
    vs = Version.objects()
    if len(vs) == 0:
        v = Version(version=0).save()
        v.reload()
    else:
        v = vs[0]

    while v.version < CODE_DB_VERSION:
        v = upgrade_db(v)


def main() -> None:
    check_db()
    parser = argparse.ArgumentParser(description='Starts test aggregation server')
    parser.add_argument('-p', '--port', action='store', type=int, default=9909,
                        help='The port on which to start the server.')
    args = parser.parse_args(sys.argv[1:])
    server = Server(args.port)
    server.start()


if __name__ == '__main__':
    main()
