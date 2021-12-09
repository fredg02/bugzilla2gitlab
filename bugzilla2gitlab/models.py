import re

from .utils import _perform_request, format_datetime, format_utc, markdown_table_row, add_user_mapping
from .config import _get_user_id

CONF = None


class IssueThread:
    """
    Everything related to an issue in GitLab, e.g. the issue itself and subsequent comments.
    """

    def __init__(self, config, fields):
        global CONF
        CONF = config
        self.load_objects(fields)

    def load_objects(self, fields):
        """
        Load the issue object and the comment objects.
        If CONF.dry_run=False, then Attachments are created in GitLab in this step.
        """
        self.issue = Issue(fields)
        self.comments = []
        """
        fields["long_desc"] gets peared down in Issue creation (above). This is because bugzilla
        lacks the concept of an issue description, so the first comment is harvested for
        the issue description, as well as any subsequent comments that are simply attachments
        from the original reporter. What remains below should be a list of genuine comments.
        """

        for comment_fields in fields["long_desc"]:
            if comment_fields.get("thetext"):
                self.comments.append(Comment(comment_fields))

    def save(self):
        """
        Save the issue and all of the comments to GitLab.
        If CONF.dry_run=True, then only the HTTP request that would be made is printed.
        """
        self.issue.save()

        for comment in self.comments:
            comment.issue_id = self.issue.id
            comment.save()

        # close the issue in GitLab, if it is resolved in Bugzilla
        if self.issue.status in CONF.bugzilla_closed_states:
            self.issue.close()


class Issue:
    """
    The issue model
    """

    required_fields = ["sudo", "title", "description"]
    data_fields = [
        "sudo",
        "created_at",
        "title",
        "description",
        "assignee_ids",
        "milestone_id",
        "labels",
        "confidential"
    ]

    def __init__(self, bugzilla_fields):
        self.headers = CONF.default_headers
        validate_user(bugzilla_fields["reporter"])
        validate_user(bugzilla_fields["assigned_to"])
        self.load_fields(bugzilla_fields)

    def load_fields(self, fields):
        if CONF.use_bugzilla_id_in_title:
            self.title = "[Bug {}] {}".format(fields["bug_id"], fields["short_desc"])
        else:
            self.title = fields["short_desc"]
        if CONF.dry_run:
          print ("Bug title: {}".format(self.title))
        self.sudo = CONF.gitlab_users[CONF.bugzilla_users[fields["reporter"]]]
        self.assignee_ids = [
            CONF.gitlab_users[CONF.bugzilla_users[fields["assigned_to"]]]
        ]
        self.created_at = format_utc(fields["creation_ts"])
        self.status = fields["bug_status"]

        #set confidential
        if fields.get("group"):
            if fields["group"] == CONF.confidential_group:
               print ("Confidential group flag is set. Will mark issue as confidential!")
               self.confidential = True

        self.create_labels(
            fields["component"], fields.get("op_sys"), fields.get("keywords"), fields["bug_severity"]
        )
        self.bug_id = fields["bug_id"]
        milestone = fields["target_milestone"]
        if CONF.map_milestones and milestone not in CONF.milestones_to_skip:
            self.create_milestone(milestone)
        self.create_description(fields)

    def create_labels(self, component, operating_system, keywords, severity):
        """
        Creates 4 types of labels: default labels listed in the configuration, component labels,
        operating system labels, and keyword labels.
        """
        labels = []
        if CONF.default_gitlab_labels:
            labels.extend(CONF.default_gitlab_labels)

        component_label = None
        if not CONF.component_mappings is None:
            component_label = CONF.component_mappings.get(component)

        if component_label is None:
            if CONF.component_mapping_auto: 
                component_label = component
            else:
                raise Exception("No component mapping found for '{}'".format(component))

        print ("Assigning component label: {}...".format(component_label))

        if component_label:
            labels.append(component_label)

        # Do not create a label if the OS is other. That is a meaningless label.
        if (
            CONF.map_operating_system
            and operating_system
            and operating_system != "Other"
        ):
            labels.append(operating_system)

        if CONF.map_keywords and keywords:
            # Input: payload of XML element like this: <keywords>SECURITY, SUPPORT</keywords>
            # Bugzilla restriction: You may not use commas or whitespace in a keyword name.
            for k in keywords.replace(" ", "").split(","):
                if not (CONF.keywords_to_skip and k in CONF.keywords_to_skip):
                    labels.append(k)

        if severity:
            if severity == "critical" or severity == "blocker":
                if severity == "critical" and CONF.severity_critical_label:
                    severity_label = CONF.severity_critical_label
                elif severity == "blocker" and CONF.severity_blocker_label:
                    severity_label = CONF.severity_blocker_label
                else:
                    severity_label = severity
                print ("Found severity '{}'. Assigning label: '{}'...".format(severity, severity_label))
                labels.append(severity_label)

        self.labels = ",".join(labels)

    def create_milestone(self, milestone):
        """
        Looks up milestone id given its title or creates a new one.
        """
        if milestone not in CONF.gitlab_milestones:
            print("Create milestone: {}".format(milestone))
            url = "{}/projects/{}/milestones".format(
                CONF.gitlab_base_url, CONF.gitlab_project_id
            )
            response = _perform_request(
                url,
                "post",
                headers=self.headers,
                data={"title": milestone},
                verify=CONF.verify,
            )
            CONF.gitlab_milestones[milestone] = response["id"]

        self.milestone_id = CONF.gitlab_milestones[milestone]

    def create_description(self, fields):
        """
        An opinionated description body creator.
        """
        ext_description = ""

        # markdown table header
        self.description = markdown_table_row("", "")
        self.description += markdown_table_row("---", "---")

        if CONF.include_bugzilla_link:
            bug_id = fields["bug_id"]
            link = "{}/show_bug.cgi?id={}".format(CONF.bugzilla_base_url, bug_id)
            self.description += markdown_table_row(
                "Bugzilla Link", "[{}]({})".format(bug_id, link)
            )

        formatted_creation_dt = format_datetime(fields["creation_ts"], CONF.datetime_format_string)
        self.description += markdown_table_row("Reported",  "{} {}".format(formatted_creation_dt, CONF.timezone))

        formatted_modification_dt = format_datetime(fields["delta_ts"], CONF.datetime_format_string)
        self.description += markdown_table_row("Modified", "{} {}".format(formatted_modification_dt, CONF.timezone))

        if fields.get("bug_status"):
            status = fields["bug_status"]
            if fields.get("resolution"):
                status += " " + fields["resolution"]
            self.description += markdown_table_row("Status", status)

        if fields.get("resolution"):
            self.description += markdown_table_row(
                "Resolved",
                "{} {}".format(format_datetime(fields["delta_ts"], CONF.datetime_format_string), CONF.timezone),
            )

        if CONF.include_version:
            self.description += markdown_table_row("Version", fields.get("version"))
        if CONF.include_os:
            self.description += markdown_table_row("OS", fields.get("op_sys"))
        if CONF.include_arch:
            self.description += markdown_table_row("Architecture", fields.get("rep_platform"))

        deplist = ""
        blocklist = ""
        see_alsolist = ""
        if fields.get("dependson"):
            for depends in fields.get("dependson"):
                link = "{}/show_bug.cgi?id={}".format(CONF.bugzilla_base_url, depends)
                deplist += "[{}]({}) ".format(depends, link)
            self.description += markdown_table_row("Depends On", deplist)
        if fields.get("blocked"):
            for blocked in fields.get("blocked"):
                link = "{}/show_bug.cgi?id={}".format(CONF.bugzilla_base_url, blocked)
                blocklist += "[{}]({}) ".format(blocked, link)
            self.description += markdown_table_row("Blocked by", blocklist)
        if fields.get("see_also"):
            for see_also in fields.get("see_also"):
                see_also = see_also.replace("{}/show_bug.cgi?id=".format(CONF.bugzilla_base_url),"")
                link = "{}/show_bug.cgi?id={}".format(CONF.bugzilla_base_url, see_also)
                see_alsolist += "[{}]({}) ".format(see_also, link)
            self.description += markdown_table_row("See also", see_alsolist)

        # add first comment to the issue description
        attachments = []
        to_delete = []
        comment0 = fields["long_desc"][0]
        if fields["reporter"] == comment0["who"] and comment0["thetext"]:
            ext_description += "\n## Description \n"
            ext_description += "\n\n".join(re.split("\n+", comment0["thetext"]))
            self.update_attachments(fields["reporter"], comment0, attachments)
            del fields["long_desc"][0]

        for i in range(0, len(fields["long_desc"])):
            comment = fields["long_desc"][i]
            if self.update_attachments(fields["reporter"], comment, attachments):
                to_delete.append(i)

        # delete comments that have already added to the issue description
        for i in reversed(to_delete):
            del fields["long_desc"][i]

        if attachments:
            self.description += markdown_table_row(
                "Attachments", ", ".join(attachments)
            )

        if ext_description:
            # for situations where the reporter is a generic or old user, specify the original
            # reporter in the description body
            if fields["reporter"] == CONF.bugzilla_auto_reporter:
                # try to get reporter email from the body
                _, part, user_data = ext_description.rpartition("Submitter was ")
                # partition found matching string
                if part:
                    regex = r"^(\S*)\s?.*$"
                    email = re.match(regex, user_data, flags=re.M).group(1)
                    self.description += markdown_table_row("Reporter", email)
            # Add original reporter to the markdown table
            elif CONF.bugzilla_users[fields["reporter"]] == CONF.gitlab_misc_user:
                self.description += markdown_table_row("Reporter", "{} ({})".format(fields["reporter_name"], fields["reporter"]))

            self.description += ext_description

        if CONF.dry_run:
            print (self.description)
            print ("\n")

    def update_attachments(self, reporter, comment, attachments):
        """
        Fetches attachments from comment if authored by reporter.
        """
        if comment.get("attachid") and comment.get("who") == reporter:
            filename = Attachment.parse_file_description(comment.get("thetext"))
            attachment_markdown = Attachment(comment.get("attachid"), filename).save()
            attachments.append(attachment_markdown)
            return True
        return False

    def validate(self):
        for field in self.required_fields:
            value = getattr(self, field)
            if not value:
                raise Exception("Missing value for required field: {}".format(field))
        return True

    def save(self):
        self.validate()
        url = "{}/projects/{}/issues".format(
            CONF.gitlab_base_url, CONF.gitlab_project_id
        )
        data = {k: v for k, v in self.__dict__.items() if k in self.data_fields}

        if CONF.use_bugzilla_id is True:
            print("Using original issue id")
            data["iid"] = self.bug_id

        self.headers["sudo"] = self.sudo

        response = _perform_request(
            url,
            "post",
            headers=self.headers,
            data=data,
            json=True,
            dry_run=CONF.dry_run,
            verify=CONF.verify,
        )

        if CONF.dry_run:
            # assign a random number so that program can continue
            self.id = 5
            return

        self.id = response["iid"]
        print("Created issue with id: {}".format(self.id))

    def close(self):
        url = "{}/projects/{}/issues/{}".format(
            CONF.gitlab_base_url, CONF.gitlab_project_id, self.id
        )
        data = {
            "state_event": "close",
        }
        self.headers["sudo"] = self.sudo

        _perform_request(
            url,
            "put",
            headers=self.headers,
            data=data,
            dry_run=CONF.dry_run,
            verify=CONF.verify,
        )


class Comment:
    """
    The comment model
    """

    required_fields = ["sudo", "body", "issue_id"]
    data_fields = ["created_at", "body"]

    def __init__(self, bugzilla_fields):
        self.headers = CONF.default_headers
        validate_user(bugzilla_fields["who"])
        self.load_fields(bugzilla_fields)

    def create_link(self, match_obj):
       if match_obj.group(1) is not None and match_obj.group(2) is not None:
          bug_id = match_obj.group(2)
          link = "{}/show_bug.cgi?id={}".format(CONF.bugzilla_base_url, bug_id)
          return "[{} {}]({})".format(match_obj.group(1), bug_id, link)

    def find_bug_links(self, text):
        # replace '[b|B]ug 12345' with markdown link
        text = re.sub(r"([b|B]ug)\s(\d{1,6})", self.create_link, text)
        return text

    def fix_quotes(self, text):
        # add extra line break after last quote line ('>')
        #TODO: replace with a one-liner regex ;)
        last_line_quote = False
        out = ""
        for line in text.split('\n'):
            if not line.startswith('>') and line and last_line_quote:
                out += "\n"
            out += line + "\n"
            last_line_quote = line.startswith('>')
        if not text.endswith('\n'):
            out = out.rstrip()
        return out

    def fix_comment(self, text):
        comment = self.find_bug_links(text)
        comment = self.fix_quotes(comment)
        # filter out hashtag in 'comment #5' to avoid linking to the wrong issues
        comment = re.sub(r"comment #(\d)", "comment \\1", comment) 
        return comment

    def load_fields(self, fields):
        self.sudo = CONF.gitlab_users[CONF.bugzilla_users[fields["who"]]]
        # if unable to comment as the original user, put username in comment body
        self.created_at = format_utc(fields["bug_when"])
        self.body = ""
        if CONF.bugzilla_users[fields["who"]] == CONF.gitlab_misc_user:
            self.body += "By {} ({})".format(fields["who_name"], fields["who"])
            if CONF.show_datetime_in_comments:
              self.body += " on "
            else:
              self.body += "\n\n"
        if CONF.show_datetime_in_comments:
            self.body += format_datetime(fields["bug_when"], CONF.datetime_format_string)
            self.body += "\n\n"

        # if this comment is actually an attachment, upload the attachment and add the
        # markdown to the comment body
        if fields.get("attachid"):
            filename = Attachment.parse_file_description(fields["thetext"])
            attachment_markdown = Attachment(fields["attachid"], filename).save()
            self.body += attachment_markdown
        else:
            self.body += self.fix_comment(fields["thetext"])

        if CONF.dry_run:
            print ("<--Comment start-->")
            print (self.body)
            print ("<--Comment end-->\n")

    def validate(self):
        for field in self.required_fields:
            value = getattr(self, field)
            if not value:
                raise Exception("Missing value for required field: {}".format(field))

    def save(self):
        self.validate()
        self.headers["sudo"] = self.sudo
        url = "{}/projects/{}/issues/{}/notes".format(
            CONF.gitlab_base_url, CONF.gitlab_project_id, self.issue_id
        )
        data = {k: v for k, v in self.__dict__.items() if k in self.data_fields}

        _perform_request(
            url,
            "post",
            headers=self.headers,
            data=data,
            json=True,
            dry_run=CONF.dry_run,
            verify=CONF.verify,
        )


class Attachment:
    """
    The attachment model
    """

    def __init__(self, bugzilla_attachment_id, file_description):
        self.id = bugzilla_attachment_id
        self.file_description = file_description
        self.headers = CONF.default_headers

    @classmethod
    def parse_file_description(cls, comment):
        regex = r"^Created attachment (\d*)\s?(.*)$"
        matches = re.match(regex, comment, flags=re.M)
        if not matches:
            raise Exception("Failed to match comment string: {}".format(comment))
        return matches.group(2)

    def parse_file_name(self, headers):
        # Use real filename to store attachment but descriptive name for issue text
        if "Content-disposition" not in headers:
            raise Exception(
                u"No file name returned for attachment {}".format(self.file_description)
            )
        # Content-disposition: application/zip; filename="mail_route.zip"
        regex = r"^.*; filename=\"(.*)\"$"
        matches = re.match(regex, headers["Content-disposition"], flags=re.M)
        if not matches:
            raise Exception(
                "Failed to match file name for string: {}".format(
                    headers["Content-disposition"]
                )
            )
        return matches.group(1)

    def parse_upload_link(self, attachment):
        if not (attachment and attachment["markdown"]):
            raise Exception(
                u"No markdown returned for upload of attachment {}".format(
                    self.file_description
                )
            )
        # ![mail_route.zip](/uploads/e943e69eb2478529f2f1c7c7ea00fb46/mail_route.zip)
        regex = r"^!?\[.*\]\((.*)\)$"
        matches = re.match(regex, attachment["markdown"], flags=re.M)
        if not matches:
            raise Exception(
                "Failed to match upload link for string: {}".format(
                    attachment["markdown"]
                )
            )
        return matches.group(1)

    def save(self):
        url = "{}/attachment.cgi?id={}".format(CONF.bugzilla_base_url, self.id)
        result = _perform_request(url, "get", json=False, verify=CONF.verify)
        filename = self.parse_file_name(result.headers)

        url = "{}/projects/{}/uploads".format(
            CONF.gitlab_base_url, CONF.gitlab_project_id
        )
        f = {"file": (filename, result.content)}
        attachment = _perform_request(
            url,
            "post",
            headers=self.headers,
            files=f,
            json=True,
            dry_run=CONF.dry_run,
            verify=CONF.verify,
        )
        # For dry run, nothing is uploaded, so upload link is faked just to let the process continue
        upload_link = (
            self.file_description
            if CONF.dry_run
            else self.parse_upload_link(attachment)
        )

        return u"[{}]({})".format(self.file_description, upload_link)

#TODO: move method to utils.py? => CONF is not defined in utils.py
def _get_gitlab_user_by_email(email):
    url = "{}/users?search={}".format(CONF.gitlab_base_url, email)
    response = _perform_request(url, "get", json=True, headers=CONF.default_headers)
    if len(response) > 1:
        #list all usernames
        userslist = ""
        for user in response:
          userslist += "{} ".format(user["username"])
        #TODO: raising exceptions wont allow batch mode
        raise Exception("Found more than one GitLab user for email {}: {}. Please add the right user manually.".format(email, userslist))
    elif len(response) == 0:
      # if no GitLab user is found, return the misc user
      # TODO: fix this more elegantly
      return CONF.gitlab_misc_user
    else:
      return response[0]["username"]

def validate_user(bugzilla_user):
    if bugzilla_user not in CONF.bugzilla_users:
        print ("Validating username {}...".format(bugzilla_user))
        gitlab_user = _get_gitlab_user_by_email(bugzilla_user)

        if gitlab_user is not None:
            print("Found GitLab user {} for Bugzilla user {}".format(gitlab_user, bugzilla_user))
            # add user to user_mappings.yml
            user_mappings_file = "{}/user_mappings.yml".format(CONF.config_path)
            add_user_mapping(user_mappings_file, bugzilla_user, gitlab_user)

            # update user mapping in memory
            CONF.bugzilla_users[bugzilla_user] = gitlab_user
            uid = _get_user_id(gitlab_user, CONF.gitlab_base_url, CONF.default_headers, verify=CONF.verify)
            CONF.gitlab_users[gitlab_user] = str(uid)
        else:
            raise Exception(
                "No matching GitLab user found for Bugzilla user `{}` "
                "Please add them before continuing.".format(bugzilla_user)
            )
