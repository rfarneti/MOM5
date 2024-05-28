
from __future__ import print_function

import os
import sys
import subprocess as sp
import shlex
import shutil
import tempfile
import time
import platform as plat

run_scripts = {}

run_scripts['nci'] = \
"""#!/bin/csh -f

#PBS -P x77
#PBS -q normal
#PBS -l walltime={walltime}
#PBS -l ncpus={ncpus}
#PBS -l mem={mem}
#PBS -l wd
#PBS -l storage=scratch/v45+scratch/x77
#PBS -o {stdout_file}
#PBS -e {stderr_file}
#PBS -N {run_name}
#PBS -W block=true

limit stacksize unlimited

./MOM_run.csh --platform nci --type {type} --experiment {exp} {npes} {valgrind}
"""

build_cmd_template = " ./MOM_compile.csh --platform {platform} --type {type} {unit_testing}"

class ModelTestSetup(object):

    def __init__(self):

        self.my_dir = os.path.dirname(os.path.realpath(__file__))
        self.exp_dir = os.path.join(self.my_dir, '../', 'exp')
        self.data_dir = os.path.join(self.my_dir, '../data')
        self.archive_dir = os.path.join(self.data_dir, 'archives')
        self.work_dir = os.path.join(self.my_dir, '../', 'work')

    def download_input_data(self, exp):
        """
        Download the experiment input data.

        This needs to be done before submitting the MOM_run.sh script because
        the compute nodes may not have Internet access.
        """

        filename = '{}.input.tar.gz'.format(exp)
        input = os.path.join(self.archive_dir, filename)

        ret = 0
        if not os.path.exists(input):
            cmd = '{} {}'.format(os.path.join(self.data_dir, 'get_exp_data.py'),
                                 filename)
            print(cmd)
            ret = sp.call(shlex.split(cmd))
        if ret != 0:
            return ret
        assert(os.path.exists(input))

        # Unzip into work directory.
        if not os.path.exists(self.work_dir):
            os.mkdir(self.work_dir)

        if not os.path.exists(os.path.join(self.work_dir, filename)):
            shutil.copy(input, self.work_dir)

        if not os.path.exists(os.path.join(self.work_dir, exp)):
            cmd = '/bin/tar -C {} -xvf {}'.format(self.work_dir, input)
            print(cmd)
            ret += sp.call(shlex.split(cmd))

        return ret


    def get_qsub_output(self, fo, fe):

        # The command has finished. Read output and write stdout.
        # We don't know when output has stopped so just keep trying
        # until it is all gone.
        empty_reads = 0
        stderr = ''
        stdout = ''
        while True:
            so = os.read(fo, 1024*1024).decode(encoding='ASCII')
            se = os.read(fe, 1024*1024).decode(encoding='ASCII')

            if so == '' and se == '':
                empty_reads += 1
            else:
                stdout += so
                stderr += se
                empty_reads = 0

            if empty_reads > 10:
                break

            time.sleep(2)

        return (stdout, stderr)


    def get_platform(self):

        # We need to get the node/platform - see if Jenkins has this set.
        platform = 'nci'

        try:
            platform = os.environ['label']
        except KeyError:
            pass

        return platform


    def run(self, model_type, exp, walltime='01:00:00', ncpus='32',
            npes=None, mem='64Gb', qsub=True, download_input_data=True,
            valgrind=False):
        """
        ncpus is for requested cpus, npes is for how many mom uses.
        """

        if download_input_data:
            ret = self.download_input_data(exp)
            if ret != 0:
                print('Error: could not download input data.',
                      file=sys.stderr)
                return (ret, None, None)

        os.chdir(self.exp_dir)

        run_name = "CI_%s" % exp
        # -N value is a maximum of 15 chars.
        run_name = run_name[0:15]

        if npes != None:
            npes = '--npes %s' % npes
        else:
            npes = ''

        if valgrind:
            valgrind = '--valgrind'
        else:
            valgrind =''

        # Get temporary file names for the stdout, stderr.
        fo, stdout_file = tempfile.mkstemp(dir=self.exp_dir, text=True)
        fe, stderr_file = tempfile.mkstemp(dir=self.exp_dir, text=True)

        # Write script out as a file.
        run_script = run_scripts[self.get_platform()]
        run_script = run_script.format(walltime=walltime, ncpus=ncpus,
                                       mem=mem, stdout_file=stdout_file,
                                       stderr_file=stderr_file,
                                       run_name=run_name,
                                       type=model_type, exp=exp, npes=npes,
                                       valgrind=valgrind)

        print(self.exp_dir)
        print(run_script)
        # Write out run script
        frun, run_file = tempfile.mkstemp(dir=self.exp_dir, text=True)
        os.write(frun, run_script.encode())
        os.close(frun)
        os.chmod(run_file, 0o755)

        # Submit the experiment. This will block until it has finished.
        ret = 0
        stdout = ''
        stderr = ''
        if qsub:
            ret = sp.call(['qsub', run_file])
            stdout, stderr = self.get_qsub_output(fo, fe)
        else:
            try:
                stdout = sp.check_output([run_file], stderr=sp.STDOUT, text=True)
            except sp.CalledProcessError as e:
                ret = e.returncode
                stdout = e.output

        os.write(fo, stdout.encode())
        os.write(fe, stderr.encode())

        os.close(fo)
        os.close(fe)

        # Move temporary files to experiment directory.
        shutil.move(stdout_file, os.path.join(self.work_dir, exp, 'fms.out'))
        shutil.move(stderr_file, os.path.join(self.work_dir, exp, 'fms.err'))
        shutil.move(run_file, os.path.join(self.work_dir, exp, 'run.sh'))

        # Change back to test dir.
        os.chdir(self.my_dir)

        return (ret, stdout, stderr)


    def build(self, model_type, unit_testing=True):

        global build_cmd_template

        os.chdir(self.exp_dir)

        if unit_testing:
            unit_testing = '--unit_testing'
        else:
            unit_testing =''

        platform = self.get_platform()
        build_cmd = build_cmd_template.format(type=model_type, platform=platform,
                                              unit_testing=unit_testing)
        # Build the model.
        ret = sp.call(shlex.split(build_cmd))

        os.chdir(self.my_dir)
        return ret
