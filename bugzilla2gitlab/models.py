import re, json, base64, logging
import gitlab
from .utils import _perform_request, format_datetime, format_utc, markdown_table_row, add_user_mapping, _get_gitlab_user_by_email, _get_user_id

CONF = None

class IssueThread:
    """
    Everything related to an issue in GitLab, e.g. the issue itself and subsequent comments.
    """

    def __init__(self, config, milestones, fields, gl, project, member_ids):
        global CONF
        CONF = config
        self.milestones = milestones
        self.project = project
        self.member_ids = member_ids
        self.gl = gl
        self.load_objects(fields)

    def load_objects(self, fields):
        """
        Load the issue object and the comment objects.
        If CONF.dry_run=False, then Attachments are created in GitLab in this step.
        """
        self.comments = []
        self.attachments = {}
        """
        fields["long_desc"] gets peared down in Issue creation (above). This is because bugzilla
        lacks the concept of an issue description, so the first comment is harvested for
        the issue description, as well as any subsequent comments that are simply attachments
        from the original reporter. What remains below should be a list of genuine comments.
        """

        if fields.get("attachment"):
            logging.info("Processing {} attachment(s)...".format(len(fields.get("attachment"))))
            for attachment_fields in fields["attachment"]:
                if attachment_fields["isobsolete"] == "1":
                    logging.info("Attachment {} is marked as obsolete.".format(attachment_fields["attachid"]))
                self.attachments[attachment_fields["attachid"]] = Attachment(attachment_fields)

        issue_attachment = {}
        if fields.get("long_desc"):
            comment0 = fields.get("long_desc")[0]
            if comment0.get("attachid"):
                issue_attachment = self.attachments.get(comment0.get("attachid"))
        self.issue = Issue(fields, self.gl, self.project, self.milestones, self.member_ids, issue_attachment)

        for comment_fields in fields["long_desc"]:
            if comment_fields.get("thetext"):
                attachment = {}
                if comment_fields.get("attachid"):
                    attachment = self.attachments.get(comment_fields.get("attachid"))
                self.comments.append(Comment(comment_fields, self.gl, self.project, self.member_ids, attachment))

    def save(self):
        """
        Save the issue and all of the comments to GitLab.
        If CONF.dry_run=True, then only the HTTP request that would be made is printed.
        """
        self.issue.save()

        if not CONF.dry_run:
            gl_issue = self.project.issues.get(self.issue.id)
        else:
	        # assign a random issue id so that program can continue
            gl_issue = 123

        for comment in self.comments:
            comment.issue_id = self.issue.id
            comment.save(gl_issue)

        # close the issue in GitLab, if it is resolved in Bugzilla
        if self.issue.status in CONF.bugzilla_closed_states:
            self.issue.close()

        # close the issue in Bugzilla
        if CONF.close_bugzilla_bugs:
            self.issue.closeBugzilla()


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

    def __init__(self, bugzilla_fields, gl, project, milestones, member_ids, attachment=None):
        self.headers = CONF.default_headers
        validate_user(gl, bugzilla_fields["reporter"])
        validate_user(gl, bugzilla_fields["assigned_to"])
        self.project = project
        self.milestones = milestones
        self.member_ids = member_ids
        self.attachment = attachment
        self.load_fields(bugzilla_fields)

    def load_fields(self, fields):
        if CONF.use_bugzilla_id_in_title:
            self.title = "[Bug {}] {}".format(fields["bug_id"], fields["short_desc"])
        else:
            self.title = fields["short_desc"]
        if CONF.dry_run:
          logging.info("Bug title: {}".format(self.title))
        self.sudo = CONF.gitlab_users[CONF.bugzilla_users[fields["reporter"]]]

        if fields["assigned_to"] in CONF.unassign_list:
            self.assignee_ids = ""
            logging.info("Found match in unassign_list, assigning issue to no one!")
        else:
            self.assignee_ids = [CONF.gitlab_users[CONF.bugzilla_users[fields["assigned_to"]]]]
            logging.info("Assigning issue to {}".format(CONF.bugzilla_users[fields["assigned_to"]]))

        self.created_at = format_utc(fields["creation_ts"])
        self.status = fields["bug_status"]

        #set confidential
        if fields.get("group"):
            if fields["group"] == CONF.confidential_group:
               logging.info("Confidential group flag is set. Will mark issue as confidential!")
               self.confidential = True

        self.create_labels(
            fields["component"], fields.get("op_sys"), fields.get("keywords"), fields["bug_severity"], fields["status_whiteboard"]
        )
        self.bug_id = fields["bug_id"]
        milestone = fields["target_milestone"]
        if CONF.map_milestones and milestone not in CONF.milestones_to_skip:
            self.create_milestone(milestone)
        self.create_description(fields)

    def create_labels(self, component, operating_system, keywords, severity, spam):
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

        logging.info("Assigning component label: {}...".format(component_label))

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
                logging.info("Found severity '{}'. Assigning label: '{}'...".format(severity, severity_label))
                labels.append(severity_label)

        if spam and spam.lower() == "spam":
           logging.info("Found keyword spam in whiteboard field! Assigning label...")
           labels.append("spam")

        self.labels = ",".join(labels)

    def create_milestone(self, milestone):
        """
        Looks up milestone id given its title or creates a new one.
        """
        if milestone not in self.milestones:
            logging.info("Create milestone: {}".format(milestone))
            if CONF.dry_run:
              # assign a random number so that program can continue
              CONF.gitlab_milestones[milestone] = 23
            else:
              gl_milestone = self.project.milestones.create({'title': milestone})
              self.milestones[milestone] = gl_milestone.id

        self.milestone_id = self.milestones[milestone]

    def show_related_bugs(self, fields):
        deplist = []
        blocklist = []
        see_alsolist = []
        if fields.get("dependson"):
            for depends in fields.get("dependson"):
                link = "{}/show_bug.cgi?id={}".format(CONF.bugzilla_base_url, depends)
                deplist.append("[{}]({})".format(depends, link))
            self.description += markdown_table_row("Depends on", ", ".join(deplist))
        if fields.get("blocked"):
            for blocked in fields.get("blocked"):
                link = "{}/show_bug.cgi?id={}".format(CONF.bugzilla_base_url, blocked)
                blocklist.append("[{}]({})".format(blocked, link))
            self.description += markdown_table_row("Blocks", ", ".join(blocklist))
        if fields.get("see_also"):
            for see_also in fields.get("see_also"):
                if CONF.see_also_gerrit_link_base_url in see_also:
                    pattern = CONF.see_also_gerrit_link_base_url + '/c/.*/\+/'
                    gerrit_id = re.sub(pattern, '', see_also)
                    see_alsolist.append("[Gerrit change {}]({})".format(gerrit_id, see_also))
                elif CONF.see_also_git_link_base_url in see_also:
                    pattern = CONF.see_also_git_link_base_url + '/.*id='
                    commit_id = re.sub(pattern, '', see_also)[0:8]
                    see_alsolist.append("[Git commit {}]({})".format(commit_id, see_also))
                else:
                    if CONF.bugzilla_base_url in see_also:
                        see_also = see_also.replace("{}/show_bug.cgi?id=".format(CONF.bugzilla_base_url),"")
                        link = "{}/show_bug.cgi?id={}".format(CONF.bugzilla_base_url, see_also)
                        see_alsolist.append("[{}]({})".format(see_also, link))
                    else:
                        see_alsolist.append(see_also)
            self.description += markdown_table_row("See also", ", ".join(see_alsolist))

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
            self.description += markdown_table_row("Bugzilla Link", "[{}]({})".format(bug_id, link))

        if fields.get("bug_status"):
            status = fields["bug_status"]
            if fields.get("resolution"):
                status += " " + fields["resolution"]
                if fields["resolution"] == "DUPLICATE":
                    status += " of [bug {}]({}/show_bug.cgi?id={})".format(fields["dup_id"], CONF.bugzilla_base_url, fields["dup_id"])
            self.description += markdown_table_row("Status", status)

        if fields.get("priority"):
            self.description += markdown_table_row("Importance", "{} {}".format(fields["priority"], fields["bug_severity"]))

        formatted_creation_dt = format_datetime(fields["creation_ts"], CONF.datetime_format_string)
        self.description += markdown_table_row("Reported",  "{} {}".format(formatted_creation_dt, CONF.timezone))

        formatted_modification_dt = format_datetime(fields["delta_ts"], CONF.datetime_format_string)
        self.description += markdown_table_row("Modified", "{} {}".format(formatted_modification_dt, CONF.timezone))

        if CONF.include_version:
            if CONF.include_version_only_when_specified:
                if fields.get("version") != "unspecified":
                    self.description += markdown_table_row("Version", fields.get("version"))
            else:
                self.description += markdown_table_row("Version", fields.get("version"))
        if CONF.include_os:
            self.description += markdown_table_row("OS", fields.get("op_sys"))
        if CONF.include_arch:
            self.description += markdown_table_row("Architecture", fields.get("rep_platform"))

        self.show_related_bugs(fields)

        # add first comment to the issue description
        attachments = []
        to_delete = []

        # deal with empty descriptions
        if fields.get("long_desc"):
            comment0 = fields["long_desc"][0]
            if fields["reporter"] == comment0["who"] and comment0["thetext"]:
                ext_description += "\n## Description \n"
                comment0_text = comment0["thetext"]
                if comment0.get("attachid"):
                    if self.attachment:
                        if not self.attachment.is_obsolete:
                            self.attachment.save(self.project) #upload the attachment!
                            ext_description += self.attachment.get_markdown(comment0_text)
                        else:
                            ext_description += re.sub(r"(attachment\s\d*)", "~~\\1~~ (attachment deleted)", comment0_text)
                    else:
                        raise Exception ("No attachment despite attachid!")
                else:
                    ext_description += comment0_text
                del fields["long_desc"][0]

            # delete comments that have already been added to the issue description
            for i in reversed(to_delete):
                del fields["long_desc"][i]

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
                        if CONF.show_email:
                            self.description += markdown_table_row("Reporter", email)
                # Add original reporter to the markdown table
                elif CONF.bugzilla_users[fields["reporter"]] == CONF.gitlab_misc_user:
                    reporter = fields["reporter_name"]
                    if CONF.show_email:
                        reporter += " ({})".format(fields["reporter"])
                    self.description += markdown_table_row("Reporter", reporter)

                self.description += self.fix_description(ext_description)
        else:
            logging.info("Description is EMPTY!")
            self.description += "\n## Description \n"
            self.description += "EMPTY DESCRIPTION"

        if CONF.dry_run:
            logging.info(self.description)
            logging.info("\n")

    def fix_description(self, text):
        text = find_bug_links(text)
        text = escape_hashtags(text)
        text = fix_newlines(text)
        return text

    def validate(self):
        for field in self.required_fields:
            value = getattr(self, field)
            if not value:
                raise Exception("Missing value for required field: {}".format(field))
        return True

    def save(self):
        self.validate()
        data = {k: v for k, v in self.__dict__.items() if k in self.data_fields}

        if CONF.use_bugzilla_id is True:
            logging.info("Using original issue id")
            data["iid"] = self.bug_id

        if not CONF.dry_run:
            # Add issue reporter as project member, temporarily
            # self.sudo needs to be converted to an int to be comparable against member_ids list
            temp_member = None
            if int(self.sudo) not in self.member_ids:
              try:
                temp_member = self.project.members.create({'user_id': self.sudo, 'access_level': gitlab.const.OWNER_ACCESS})
              except gitlab.exceptions.GitlabCreateError as e:
                #print(e + ", id: " + str(self.sudo))
                print("GitlabCreateError, id: " + str(self.sudo))
            else:
                # Give the issue reporter project owner permissions, temporarily
                member = self.project.members.get(self.sudo)
                orig_access_level = member.access_level
                member.access_level = gitlab.const.OWNER_ACCESS
                member.save()

            self.gl_issue = self.project.issues.create(data)

            # Remove issue reporter as project member,
            if temp_member is not None:
                self.project.members.delete(self.sudo)
            else:
                #TODO: make sure temporary owner permission is always removed, even if there is an exception (trap?)
                member.access_level = orig_access_level
                member.save()
        else:
            # assign a random number so that program can continue
            print ("DRY-RUN: " + str(data))
            self.id = 5
            return

        self.id = self.gl_issue.iid
        print("Created GitLab issue with id: {}, {}/{}/-/issues/{}".format(self.id, "https://gitlab.eclipse.org", CONF.gitlab_project_name, self.id))
        logging.info("Created GitLab issue with id: {}".format(self.id))

    def who_closed_the_bug(self, bug_id):
        url = "{}/rest/bug/{}/history?api_key={}".format(CONF.bugzilla_base_url, bug_id, CONF.bugzilla_api_token)
        response = _perform_request(url, "get", json=True)
        last_change = response["bugs"][0]["history"][-1]
        return last_change["who"]

    def close(self):
        who = self.who_closed_the_bug(self.bug_id)
        logging.info("who closed the bug: {}".format(who))
        #print ("self.sudo ID: {}".format(self.sudo))
        #print ("who ID: {}".format(CONF.gitlab_users[CONF.bugzilla_users[who]]))

        if who is not None:
            close_sudo = CONF.gitlab_users[CONF.bugzilla_users[who]]
        else:
            close_sudo = self.sudo

        self.gl_issue.state_event = 'close'
        self.gl_issue.save(sudo=close_sudo)
        #TODO: fix date and time of closing

    def closeBugzilla(self):
        # set status to CLOSED MOVED and post comment at the same time
        # PUT /rest/bug/(id_or_alias)

        if CONF.dry_run:
            logging.info("Bugzilla issue has been closed (DRY-RUN MODE).\n")
            return

        # TODO: works only with CONF.gitlab_project_name (otherwise an extra look-up is required)
        gitlab_url = CONF.gitlab_base_url.replace('api/v4','')
        issue_in_gitlab = "{}{}/-/issues/{}".format(gitlab_url, CONF.gitlab_project_name, self.id)
        comment = {}
        comment["comment"] = "This issue has been migrated to {}.".format(issue_in_gitlab)
        comment["is_private"] = False
        data = {}
        data["id"] = self.bug_id
        data["status"] = "CLOSED"
        data["resolution"] = "MOVED"
        data["comment"] = comment

        json_data = json.dumps(data)
        #print (json.dumps(data, indent=4))

        url = "{}/rest/bug/{}?api_key={}".format(CONF.bugzilla_base_url, self.bug_id, CONF.bugzilla_api_token)

        #Head request to avoid 'Remote end closed connection without response' (most likely due to race-condition with "Keep-Alive" option set on server)
        response_head = _perform_request(url, "head", json=False)
        logging.info("Head-Response:")
        logging.info(response_head)

        response = _perform_request(url, "put", data=json_data, headers={"Content-Type": "application/json"}, json=True)
        if response.get("error"):
            logging.error("Response:")
            logging.error(json.dumps(response, indent=4))
        else:
            logging.info("Bugzilla issue has been closed.\n")

class Comment:
    """
    The comment model
    """

    required_fields = ["sudo", "body", "issue_id"]
    data_fields = ["sudo", "created_at", "body"]

    def __init__(self, bugzilla_fields, gl, project, member_ids, attachment=None):
        self.headers = CONF.default_headers
        self.attachment = attachment
        self.project = project
        self.member_ids = member_ids
        validate_user(gl, bugzilla_fields["who"])
        self.load_fields(bugzilla_fields)

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
        text = escape_hashtags(text)
        text = find_bug_links(text)
        text = self.fix_quotes(text)
        text = fix_newlines(text)
        return text

    def load_fields(self, fields):
        self.sudo = CONF.gitlab_users[CONF.bugzilla_users[fields["who"]]] # GitLab user ID
        # if unable to comment as the original user, put user name in comment body
        self.created_at = format_utc(fields["bug_when"])
        self.body = ""
        if CONF.bugzilla_users[fields["who"]] == CONF.gitlab_misc_user and fields["who"] != CONF.bugzilla_misc_user:
            self.body += "By {}".format(fields["who_name"])
            if CONF.show_email:
                self.body += " ({})".format(fields["who"])
            if CONF.show_datetime_in_comments:
                self.body += " on "
            else:
                self.body += "\n\n"
        if CONF.show_datetime_in_comments:
            self.body += format_datetime(fields["bug_when"], CONF.datetime_format_string)
            self.body += "\n\n"

        # if this comment is actually an attachment, upload the attachment and add the markdown to the comment body
        if fields.get("attachid"):
            if self.attachment:
                if not self.attachment.is_obsolete:
                    self.attachment.save(self.project) #upload the attachment!
                    self.body += self.attachment.get_markdown(fields["thetext"])
                else:
                    self.body += self.fix_comment(re.sub(r"(attachment\s\d*)", "~~\\1~~ (attachment deleted)", fields["thetext"]))
            else:
               raise Exception ("No attachment despite attachid!")
        else:
            self.body += self.fix_comment(fields["thetext"])

        if CONF.dry_run:
            logging.info("<--Comment start-->")
            logging.info(self.body)
            logging.info("<--Comment end-->\n")

    def validate(self):
        for field in self.required_fields:
            value = getattr(self, field)
            if not value:
                raise Exception("Missing value for required field: {}".format(field))

    def save(self, gl_issue):
        self.validate()
        data = {k: v for k, v in self.__dict__.items() if k in self.data_fields}

        if not CONF.dry_run:

            # Add commenter as project member, temporarily
            # self.sudo needs to be converted to an int to be comparable against member_ids list
            temp_member = None
            if int(self.sudo) not in self.member_ids:
                try:
                  temp_member = self.project.members.create({'user_id': self.sudo, 'access_level': gitlab.const.OWNER_ACCESS})
                except gitlab.exceptions.GitlabCreateError as e:
                  #print(e + ", id: " + str(self.sudo))
                  print("GitlabCreateError, id: " + str(self.sudo))
            else:
                # Give the issue reporter project owner permissions, temporarily
                member = self.project.members.get(self.sudo)
                orig_access_level = member.access_level
                member.access_level = gitlab.const.OWNER_ACCESS
                member.save()

            #TODO: check if comment has been created already and skip it
            #print (data)
            comment = gl_issue.notes.create(data)

            # Remove issue reporter as project member,
            if temp_member is not None:
                self.project.members.delete(self.sudo)
            else:
                #TODO: make sure temporary owner permission is always removed, even if there is an exception (trap?)
                member.access_level = orig_access_level
                member.save()
        else:
            print ("DRY-RUN: " + str(data))
            return

        print("  Created comment")
        logging.info("Created comment")

class Attachment:
    """
    The attachment model
    """

    def __init__(self, fields):
        self.is_obsolete = fields["isobsolete"] == "1"
        self.id = fields["attachid"]
        self.file_name = fields["filename"]
        self.file_type = fields["type"]
        self.file_description = fields["desc"]
        if not self.is_obsolete:
            self.file_data = base64.b64decode(fields["data"])
        self.headers = CONF.default_headers
        self.upload_link = ""

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

    def get_markdown(self, comment):
        comment = re.sub(r"(attachment\s\d*)", u"[\\1]({})".format(self.upload_link), comment)
        thumbnail_size = "150"
        if self.file_type.startswith("image"):
            comment += "\n\n<img src=\"{}\" width=\"{}\" alt=\"{}\">\n\n{}".format(self.upload_link, thumbnail_size, self.file_name, self.file_name)
        else:
            comment += "\n\n"
            if self.file_type.startswith("text"):
                comment += ":notepad_spiral: "
            elif "zip" in self.file_type or "7z" in self.file_type or "rar" in self.file_type or "tar" in self.file_type:
                comment += ":compression: "
            elif self.file_type == "application/octet-stream" and self.file_name.endswith(".zip"):
                comment += ":compression: "
            comment += u"[{}]({})".format(self.file_name, self.upload_link)
        return comment

    def save(self, project):
        if not self.file_data:
            raise Exception("Attachment data is empty!")

        # For dry run, nothing is uploaded, so upload link is faked just to let the process continue
        if CONF.dry_run:
            self.upload_link = "/dry-run/upload-link"
        else:
            attachment = project.upload(self.file_name, filedata=self.file_data)
            self.upload_link = self.parse_upload_link(attachment)

def validate_user(gl, bugzilla_user):
    if bugzilla_user not in CONF.bugzilla_users:
        logging.info("Validating username {}...".format(bugzilla_user))
        gitlab_user = _get_gitlab_user_by_email(gl, bugzilla_user)

        if gitlab_user is None:
            # if no GitLab user is found, return the bugzilla_misc_user
            print("No matching GitLab user found for Bugzilla user `{}`. Using bugzilla_misc_user instead.".format(bugzilla_user))
            logging.info("No matching GitLab user found for Bugzilla user `{}`. Using bugzilla_misc_user instead.".format(bugzilla_user))
            gitlab_user = _get_gitlab_user_by_email(gl, CONF.bugzilla_misc_user)

        if gitlab_user is not None:
            logging.info("Found GitLab user {} for Bugzilla user {}".format(gitlab_user.username, bugzilla_user))
            # add user to user_mappings.yml
            user_mappings_file = "{}/user_mappings.yml".format(CONF.config_path)
            add_user_mapping(user_mappings_file, bugzilla_user, gitlab_user.username)

            # update user mapping in memory
            #print ("GitLab username: " + str(gitlab_user.username))
            CONF.bugzilla_users[bugzilla_user] = gitlab_user.username
            #print ("GitLab user ID: " + str(gitlab_user.id))
            CONF.gitlab_users[gitlab_user.username] = str(gitlab_user.id)
        else:
            raise Exception(
                "No matching GitLab user found for Bugzilla user `{}` "
                "Please add them before continuing.".format(bugzilla_user)
            )

def fix_newlines(text):
    # fix line breaks in markdown syntax
    out = ""
    split_list = text.split('\n')
    nl = re.compile('^\d*[\.\)]') # regex pattern to match numbered list
    for index, line in enumerate(split_list):
        if index < len(split_list)-1:
            next_line = split_list[index+1]
            if len(line) > 0 and len(next_line) > 0:
                if not next_line.strip().startswith(('> ','* ','- ','#')) and not line.strip().startswith(('> ','#')) and not nl.match(next_line.strip()):
                    out += line + '\\\n'
                else:
                    out += line + '\n'
            else:
                out += line + '\n'
    else:
        out += line
    return out

def create_link(match_obj):
    if match_obj.group(4) is not None:
        bug_id = match_obj.group(2)
        comment_no = match_obj.group(6)
        link = "{}/show_bug.cgi?id={}#c{}".format(CONF.bugzilla_base_url, bug_id, comment_no)
        return "[{} {} {}]({})".format(match_obj.group(1), bug_id, match_obj.group(4), link)
    else:
        bug_id = match_obj.group(2)
        link = "{}/show_bug.cgi?id={}".format(CONF.bugzilla_base_url, bug_id)
        return "[{} {}]({}){}".format(match_obj.group(1), bug_id, link, match_obj.group(3))

def find_bug_links(text):
    # replace '[b|B]ug 12345 [c|C]omment 1' with markdown link
    text = re.sub(r"([b|B]ug)\s(\d{1,6})(\s?)(([c|C]omment)\s\\?\#?(\d{1,6}))?", create_link, text)
    return text

def escape_hashtags(text):
    # escape hashtag in '#5' to avoid linking to the wrong issues
    text = re.sub(r"\#(\d)", "\#\\1", text)
    return text

