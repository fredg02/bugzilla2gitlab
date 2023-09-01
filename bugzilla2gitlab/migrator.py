import os
import gitlab
from .config import get_config, _load_milestone_id_cache
from .models import IssueThread
from .utils import bugzilla_login, get_bugzilla_bug, load_bugzilla_bug, validate_list, fetch_bug_list, save_bug_list, _get_gitlab_user_by_email


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
            #login to GitLab
            #strip api path
            gitlab_url = self.conf.gitlab_base_url.replace('/api/v4','')
            self.gl = gitlab.Gitlab(url=gitlab_url, private_token=self.conf.gitlab_private_token)
            self.gl.auth()
            #self.gl.enable_debug()

            bugzilla_misc_user = _get_gitlab_user_by_email(self.gl, self.conf.bugzilla_misc_user)
            bugzilla_misc_user_id = bugzilla_misc_user.id

            self.project = self.gl.projects.get(self.conf.gitlab_project_id)

            if self.conf.map_milestones:
                self.milestones = _load_milestone_id_cache(self.project)

            # Add bugzilla_misc_user_id as project member - temporarily
            # TODO: can this be done more elegantly?
            self.member_ids = []
            for m in self.project.members_all.list(get_all=True):
              self.member_ids.append(m.id)
            #print(self.member_ids)
            #print(bugzilla_misc_user_id)
            #if int(bugzilla_misc_user_id) not in self.member_ids:
            #  member = self.project.members.create({'user_id': bugzilla_misc_user_id, 'access_level': gitlab.const.REPORTER_ACCESS})

            try:
              member = self.project.members.create({'user_id': bugzilla_misc_user_id, 'access_level': gitlab.const.REPORTER_ACCESS})
            except gitlab.exceptions.GitlabCreateError as e:
              #print(e + ", id: " + str(bugzilla_misc_user_id))
              print("GitlabCreateError, id: " + str(bugzilla_misc_user_id))

            for bug in bug_list:
                if bug:
                    self.migrate_one(bug)

            # Remove bugzilla_misc_user_id from project again
            self.project.members.delete(bugzilla_misc_user_id)

    def migrate_one_file(self, file):
        """
        Migrate a single bug from Bugzilla to GitLab. TEST MODE
        """
        print("Migrating file {}".format(file))
        fields = load_bugzilla_bug(file)
        issue_thread = IssueThread(self.conf, self.milestones, fields, self.gl, self.project, self.member_ids)
        issue_thread.save()

    def migrate_one(self, bugzilla_bug_id):
        """
        Migrate a single bug from Bugzilla to GitLab.
        """
        print("Migrating bug {}".format(bugzilla_bug_id))
        fields = get_bugzilla_bug(self.conf.bugzilla_base_url, bugzilla_bug_id)
        issue_thread = IssueThread(self.conf, self.milestones, fields, self.gl, self.project, self.member_ids)
        issue_thread.save()
