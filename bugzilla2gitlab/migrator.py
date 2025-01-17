import os
from .config import get_config
from .models import IssueThread
from .utils import bugzilla_login, get_bugzilla_bug, load_bugzilla_bug, validate_list, fetch_bug_list, save_bug_list


class Migrator:
    def __init__(self, config_path):
        self.conf = get_config(config_path)

    def migrate(self, bug_list):
        """
        Migrate a list of bug ids from Bugzilla to GitLab.
        """

        if self.conf.bugzilla_user:
            bugzilla_login(
                self.conf.bugzilla_base_url,
                self.conf.bugzilla_user,
                self.conf.bugzilla_password,
            )

        if self.conf.fetch_bugs:
            bug_list = fetch_bug_list(self.conf.bugzilla_base_url,
                       self.conf.bugzilla_api_token,
                       self.conf.bugzilla_product,
                       self.conf.bugzilla_components,
                       self.conf.bugzilla_bug_status,
                       self.conf.max_no_of_bugs)

            #TODO: is storing bugs even necessary?
            save_bug_list(bug_list, self.conf.buglist_file)

        validate_list(bug_list)

        if self.conf.test_mode:
            print ("### TEST MODE ###")
            test_dir = os.path.join(self.conf.config_path, "test_xmls")
            for file in os.listdir(test_dir):
                if file.endswith(".xml"):
                    self.migrate_one_file(os.path.join(test_dir, file))
        else:
            for bug in bug_list:
                if bug:
                    self.migrate_one(bug)

    def migrate_one_file(self, file):
        """
        Migrate a single bug from Bugzilla to GitLab. TEST MODE
        """
        print("Migrating file {}".format(file))
        fields = load_bugzilla_bug(file)
        issue_thread = IssueThread(self.conf, fields)
        issue_thread.save()

    def migrate_one(self, bugzilla_bug_id):
        """
        Migrate a single bug from Bugzilla to GitLab.
        """
        print("Migrating bug {}".format(bugzilla_bug_id))
        fields = get_bugzilla_bug(self.conf.bugzilla_base_url, bugzilla_bug_id)
        issue_thread = IssueThread(self.conf, fields)
        issue_thread.save()
