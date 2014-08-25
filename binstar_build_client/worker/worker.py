"""
The worker 
"""
from binstar_build_client.worker.utils.buffered_io import BufferedPopen
from binstar_build_client.worker.utils.script_generator import gen_build_script, \
    get_list
from binstar_build_client.worker.build_log import BuildLog
from binstar_client import errors
from contextlib import contextmanager
from pprint import pprint
from subprocess import STDOUT
import logging
import os
import time
import traceback
import yaml

log = logging.getLogger('binstar.build')

class Worker(object):
    """
    
    """
    STATE_FILE = 'worker.yaml'
    JOURNAL_FILE = 'journal.csv'
    SLEEP_TIME = 10

    def __init__(self, bs, args):
        self.bs = bs
        self.args = args

    def work_forever(self):
        """
        Start a loop and continuously build forever
        """
        with self.worker_context() as worker_id:
            self.worker_id = worker_id
            self._build_loop()


    def _build_loop(self):
        """
        This is the main build loop this checks binstar.org for any jobs it can do and 
        """

        bs = self.bs
        args = self.args
        sleep_time = 5
        with open(self.JOURNAL_FILE, 'a') as journal:
            while 1:
                try:
                    job_data = bs.pop_build_job(args.username, args.queue, self.worker_id)
                except errors.NotFound:
                    if args.show_traceback:
                        raise
                    else:
                        msg = ("This worker can no longer pop items off the build queue. "
                               "Did someone remove it manually?")
                        raise errors.BinstarError(msg)
                if job_data.get('job') is None:
                    time.sleep(self.SLEEP_TIME)
                    continue
                ctx = (job_data['job']['_id'], job_data['job_name'])
                log.info('Starting build, %s, %s\n' % ctx)
                journal.write('starting build, %s, %s\n' % ctx)

                try:
                    failed, status = self.build(job_data)
                except Exception:
#                     bs.push_build_job(args.username, args.queue, self.worker_id, job_data['job']['_id'])
#                     raise
                    job_data = bs.fininsh_build(args.username, args.queue, self.worker_id, job_data['job']['_id'],
                                                failed=True, status='error')
                    traceback.print_exc()
                else:
                    job_data = bs.fininsh_build(args.username, args.queue, self.worker_id, job_data['job']['_id'],
                                                failed=failed, status=status)

                finally:
                    journal.write('finished build, %s, %s\n' % ctx)

    def build(self, job_data):
        """
        Run a single build 
        """
        job_id = job_data['job']['_id']
        build_log = BuildLog(self.bs, self.args.username, self.args.queue, self.worker_id, job_id)

        build_log.write("Building on worker %s (platform %s)\n" % (self.args.hostname, self.args.platform))
        build_log.write("Starting build %s\n" % job_data['job_name'])
        pprint(job_data)

        if not os.path.exists('build_scripts'):
            os.mkdir('build_scripts')

        build_script = gen_build_script(job_data)

        script_filename = os.path.join('build_scripts', '%s.sh' % job_id)
        with open(script_filename, 'w') as fd:
            fd.write(build_script)

        iotimeout = job_data['build_item_info'].get('instructions').get('iotimeout', 60 * 5)
        args = ['bash', script_filename, '--api-token', job_data['upload_token']]

        if job_data.get('git_oauth_token'):
            args.extend(['--git-oauth-token', job_data.get('git_oauth_token')])
        else:
            build_filename = self.download_build_source(job_id)
            args.extend(['--build-tarball', build_filename])

        log.info("Running command:")
        log.info(" ".join(args))
        p0 = BufferedPopen(args, iotimeout=iotimeout, stdout=build_log, stderr=STDOUT)
        exit_code = p0.wait()

        log.info("Build script exited with code %s" % exit_code)
        if exit_code == 0:
            failed = False
            status = 'success'
            log.info('Build %s Succeeded' % (job_data['job_name']))
        elif exit_code == 11:
            failed = True
            status = 'error'
            log.error("Build %s errored" % (job_data['job_name']))
        elif exit_code == 12:
            failed = True
            status = 'failure'
            log.error("Build %s failed" % (job_data['job_name']))
        else:  # Unknown error
            failed = True
            status = 'error'
            log.error("Unknown build exit status %s for build %s" % (exit_code, self.build['_id']))

        return failed, status

    def download_build_source(self, job_id):
        """
        If the source files for this job were tarred and uploaded to bisntar.
        Download them. 
        """
        log.info("Fetching build data")
        if not os.path.exists('build_data'):
            os.mkdir('build_data')

        build_filename = os.path.join('build_data', '%s.tar.bz2' % job_id)
        fp = self.bs.fetch_build_source(self.args.username, self.args.queue, self.worker_id, job_id)

        with open(build_filename, 'wb') as bp:
            data = fp.read(2 ** 13)
            while data:
                bp.write(data)
                data = fp.read(2 ** 13)

        log.info("Wrote build data to %s" % build_filename)
        return os.path.abspath(build_filename)


    @contextmanager
    def worker_context(self):
        '''
        Register the worker with binstar and clean up on any excpetion or exit
        '''
        os.chdir(self.args.cwd)

        if os.path.isfile(self.STATE_FILE):
            with open(self.STATE_FILE, 'r') as fd:
                worker_data = yaml.load(fd)
            if self.args.clean:
                self.bs.remove_worker(self.args.username, self.args.queue, worker_data['worker_id'])
                log.info("Un-registered worker %s from binstar site" % worker_data['worker_id'])
                os.unlink(self.STATE_FILE)
                log.info("Removed worker.yaml")
                raise SystemExit()
            else:
                raise errors.UserError("Lock file '%s' exists. Use -c/--clean to remove this working context" % self.STATE_FILE)

        worker_id = self.bs.register_worker(self.args.username, self.args.queue, self.args.platform, self.args.hostname)
        worker_data = {'worker_id': worker_id}

        with open(self.STATE_FILE, 'w') as fd:
            yaml.dump(worker_data, fd)
        try:
            yield worker_id
        finally:
            log.info("Removing worker %s" % worker_id)
            self.bs.remove_worker(self.args.username, self.args.queue, worker_id)
            os.unlink(self.STATE_FILE)
            log.debug("Removed %s" % self.STATE_FILE)
