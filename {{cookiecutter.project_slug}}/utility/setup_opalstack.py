"""
Opalstack setup script for projects configured as per cookiecutter-django-opalstack
"""
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from random import randint
from tempfile import NamedTemporaryFile


class OpalstackHelper:
    """
    OpalstackHelper class is just a container. All steps are run during the __init__ method. Each step is a method.
    """

    def __init__(self):
        """
        Install requirements.
        Create .env file from input.
        Migrate database.
        Collect static files.
        Configure uwsgi.ini
        Create cronjob for memcached.
        Restart wsgi server.
        Optionally configure database backups.
        """
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__file__)

        self.root_path = Path(__file__).resolve().parent.parent
        self.parent_path = self.root_path.parent

        sys.path.append(str(self.root_path))

        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

        self.install_requirements()

        # Import locally after requirements installed incase Django is now a different version.
        import django
        from django.conf import settings
        from django.core.exceptions import ImproperlyConfigured
        from django.core.management import call_command

        self.django = django
        self.settings = settings
        self.ImproperlyConfigured = ImproperlyConfigured
        self.call_command = call_command

        # create dotenv
        self.create_dotenv()

        # migrate db
        self.logger.info("Migrating database...")
        self.try_command("migrate")
        self.logger.info("Database migrated!")

        # collectstatic
        self.logger.info("Collecting static files...")
        self.try_command("collectstatic")
        self.logger.info("Static files collected!")

        # configure the uwsgi.ini file
        self.configure_uwsgi()

        # configure memcached if required
        if "memcached" in settings.CACHES.get("default", {}).get("BACKEND", ""):
            self.configure_memcached()

        # restart the server
        self.restart_wsgi()

        # optionally configure db backups
        if input("Configure daily database backups? [y/N] :").lower().startswith("y"):
            self.configure_db_backups()

        self.logger.info("Opalstack setup complete!!! Goodbye!")

    def install_requirements(self):
        """
        Install requirements into the current venv from requirements/production.txt
        :return:
        """
        self.logger.info("Installing requirements from requirements/production.txt...")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-r",
                f"{self.root_path / 'requirements' / 'production.txt'}",
            ]
        )
        self.logger.info("Requirements installed!")

    def create_dotenv(self):
        """
        Write environment variables to .env file.
        The user is required to input the value for each environment variable.
        :return:
        """
        self.logger.info("Creating .env file...")
        dotenv_path = self.root_path / ".env"

        # Write to .env with all environment variables taken in from raw input
        file_contents = ""
        while True:
            try:
                self.django.setup()
                self.logger.info(
                    "All environment variables have been specified. Well done!"
                )
                break
            except self.ImproperlyConfigured as e:
                # regex the error message and set the env variable via raw input
                regex = re.match(r"^Set the ([A-Z_]+) environment variable$", str(e))
                if not re.match:
                    raise e

                env_var = regex.group(1)
                env_val = input(f"Enter the value for {env_var}: ")

                file_contents += f"{env_var}={env_val}\n"

                os.environ.setdefault(env_var, env_val)

        # append the new env variable to the .env file
        self.logger.info("Writing environment variables to .env file.")
        with dotenv_path.open("a") as env_file:
            env_file.write(file_contents)

        self.logger.info(".env file created!")

    def configure_uwsgi(self):
        """
        Rewrite the ../uwsgi.ini file with correct paths for this project.
        :return:
        """
        self.logger.info("Configuring uwsgi.ini...")

        uwsgi_path = self.parent_path / "uwsgi.ini"
        text = uwsgi_path.read_text()
        text = text.replace(
            "/myproject/myproject/wsgi.py", f"/{self.root_path.name}/config/wsgi.py"
        )
        text = text.replace("/myproject", f"/{self.root_path.name}")
        uwsgi_path.write_text(text)

        self.logger.info("uwsgi.ini configured!")

    def configure_memcached(self):
        """
        Create a cronjob for starting and persisting memcached.
        :return:
        """
        self.logger.info("Configuring memcached...")

        cronjob = (
            '* * * * * /usr/bin/pgrep -f "memcached -d -s $HOME/apps/{parent_path}/memcached.sock" > '
            "/dev/null || memcached -d -s $HOME/apps/{parent_path}/memcached.sock -P "
            "$HOME/apps/{parent_path}/memcached.pid -M 100\n"
        ).format(parent_path=self.parent_path.name)

        self.add_cronjob(cronjob)

        self.logger.info("Memcached configured!")

    def try_command(self, command):
        """
        Try to call a management command.  Log any errors but fail silently.
        :param command: String: management command
        :return: Bool: False if exception occurred else True
        """
        try:
            self.call_command(command)
            return True
        except Exception as e:
            self.logger.warning(
                f"The following exception occurred when trying to run manage.py {command}:\n{e}"
            )
            return False

    def restart_wsgi(self):
        """
        Restart the WSGI server.
        :return:
        """
        self.logger.info("Restaring WSGI server...")
        subprocess.run([f"{self.parent_path / 'stop'}"])
        subprocess.run([f"{self.parent_path / 'start'}"])
        self.logger.info("WSGI server restarted!")

    def configure_db_backups(self):
        """
        Configure database backups as per Opalstack docs.
        https://docs.opalstack.com/user-guide/postgresql-databases/#scheduled-backups-for-postgresql-databases
        :return:
        """
        self.logger.info("Configuring daily database backups...")

        # Set up some variables
        home_path = Path.home()

        db = self.settings.DATABASES["default"]
        db_name = db["NAME"]
        db_user = db["USER"]
        db_password = db["PASSWORD"]

        script_path = home_path / ".local" / "bin" / f"backup_{db_name}"
        pgpass_path = home_path / ".pgpass"

        paths_700 = [
            home_path / ".local" / "bin",
            home_path / ".local" / "etc" / "psql_backups",
            home_path / "backups" / "psql",
        ]
        files_700 = [script_path]
        files_600 = [pgpass_path]

        # Create directories and files.  Chmod as required.
        self.make_paths(paths_700)
        self.make_files(files_700)
        self.make_files(files_600, mode=0o600)

        # Write the pgpass and backup script files
        pgpass_path.write_text(f"localhost:5432:{db_name}:{db_user}:{db_password}")
        script_path.write_text(
            self.db_backup_script.format(db_name=db_name, db_user=db_user)
        )

        # Backup the database
        backup_cmd = f"$HOME/.local/bin/backup_{db_name}"
        subprocess.run([backup_cmd.replace("$HOME", str(home_path))])

        # Add a cronjob for a random minute and hour between 0 and 5
        cronjob = f"{randint(0, 59)} {randint(0, 5)} * * * {backup_cmd}\n"
        self.add_cronjob(cronjob)

        self.logger.info("Daily database backups configured!")

    def make_paths(self, paths, mode=0o700):
        """
        Make all paths in paths list and chmod them to the desired value.
        :param paths:
        :param mode:
        :return:
        """
        for path in paths:
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(mode)

    def make_files(self, files, mode=0o700):
        """
        Make all files in files list and chmod them to the desired value.
        """
        for file in files:
            file.touch(mode=mode, exist_ok=True)
            file.chmod(mode)

    def add_cronjob(self, cronjob):
        """
        Add a cronjob to crontab.
        :param cronjob:
        :return:
        """
        crontab = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True
        ).stdout

        # Prevent duplicate commands in crontab
        if cronjob[15:] in crontab:
            return

        crontab += cronjob

        with NamedTemporaryFile("w") as tmp_file:
            tmp_file.write(crontab)
            tmp_file.flush()
            subprocess.run(["crontab", tmp_file.name])

    @property
    def db_backup_script(self):
        """
        Just keeping this in a property to keep it out the way near the end of the class.
        The script is taken from:
        https://docs.opalstack.com/user-guide/postgresql-databases/#scheduled-backups-for-postgresql-databases
        :return:
        """
        return (
            "#!/bin/bash\n"
            "\n"
            "export DBNAME={db_name}\n"
            "export DBUSER={db_user}\n"
            "\n"
            "/bin/pg_dump -b -Fp -U $DBUSER $DBNAME \\\n"
            "> $HOME/backups/psql/$DBNAME-$(date +\%Y\%m\%d\%H\%M).sql \\\n"  # noqa
            "2>> $HOME/backups/psql/$DBNAME.log"
        )


if __name__ == "__main__":
    OpalstackHelper()
