from .config import get_config
from .models import IssueThread
from .utils import bugzilla_login, get_bugzilla_bug, validate_list, fetch_bug_list, save_bug_list


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

        for bug in bug_list:
            self.migrate_one(bug)

    def migrate_one(self, bugzilla_bug_id):
        """
        Migrate a single bug from Bugzilla to GitLab.
        """
        print("Migrating bug {}".format(bugzilla_bug_id))
        fields = get_bugzilla_bug(self.conf.bugzilla_base_url, bugzilla_bug_id)
        issue_thread = IssueThread(self.conf, fields)
        issue_thread.save()
