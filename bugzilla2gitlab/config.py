from collections import namedtuple
import os

import yaml

from .utils import _perform_request, get_gitlab_project_id

Config = namedtuple(
    "Config",
    [
        "gitlab_base_url",
        "gitlab_project_id",
        "gitlab_project_name",
        "bugzilla_base_url",
        "bugzilla_user",
        "bugzilla_password",
        "bugzilla_api_token",
        "bugzilla_auto_reporter",
        "bugzilla_closed_states",
        "bugzilla_product",
        "bugzilla_components",
        "bugzilla_bug_status",
        "bugzilla_misc_user",
        "fetch_bugs",
        "max_no_of_bugs",
        "buglist_file",
        "default_headers",
        "component_mappings",
        "component_mapping_auto",
        "bugzilla_users",
        "gitlab_users",
        "gitlab_misc_user",
        "default_gitlab_labels",
        "severity_critical_label",
        "severity_blocker_label",
        "show_datetime_in_comments",
        "show_email",
        "datetime_format_string",
        "map_operating_system",
        "map_keywords",
        "keywords_to_skip",
        "map_milestones",
        "milestones_to_skip",
        "gitlab_milestones",
        "dry_run",
        "include_bugzilla_link",
        "include_version",
        "include_version_only_when_specified",
        "include_os",
        "include_arch",
        "use_bugzilla_id",
        "use_bugzilla_id_in_title",
        "verify",
        "config_path",
        "confidential_group",
        "timezone",
        "unassign_list",
        "close_bugzilla_bugs",
        "see_also_gerrit_link_base_url",
        "see_also_git_link_base_url",
        "test_mode"
        
    ],
)


def get_config(path):
    configuration = {}
    configuration.update(_load_defaults(path))
    configuration.update(
        _load_user_id_cache(
            path,
            configuration["gitlab_base_url"],
            configuration["default_headers"],
            configuration["verify"],
        )
    )
    if configuration["map_milestones"]:
        configuration.update(
            _load_milestone_id_cache(
                configuration["gitlab_project_id"],
                configuration["gitlab_base_url"],
                configuration["default_headers"],
                configuration["verify"],
            )
        )
    configuration.update(_load_component_mappings(path))
    
    temp = {}
    temp["config_path"] = path
    configuration.update(temp)

    configuration.update(_load_unassign_list(path))

    return Config(**configuration)


def _load_defaults(path):
    with open(os.path.join(path, "defaults.yml")) as f:
        config = yaml.safe_load(f)

    defaults = {}

    #TODO: clean up
    defaults["default_headers"] = {"private-token": config["gitlab_private_token"]}

    for key in config:
        if key == "gitlab_private_token":
            continue
        if key == "gitlab_project_id":
            if config[key] is None:
                if config["gitlab_project_name"] is None:
                    raise Exception("Either 'gitlab_project_id' or 'gitlab_project_name' must be set in config file!")
                # if no gitlab_project_id is given, look_up id for gitlab_project_name
                gitlab_project_id = get_gitlab_project_id(
                                                            config["gitlab_base_url"],
                                                            config["gitlab_project_name"],
                                                            defaults["default_headers"])
                print ("Found GitLab project ID: {} for {}".format(gitlab_project_id, config["gitlab_project_name"]))
                defaults[key] = gitlab_project_id
            else:
                print ("Using GitLab project ID: {}".format(config["gitlab_project_id"]))
                defaults[key] = config[key]
        else:
            defaults[key] = config[key]

    return defaults


def _load_user_id_cache(path, gitlab_url, gitlab_headers, verify):
    """
    Load cache of GitLab usernames and ids
    """
    print("Loading user cache...")
    user_mappings_file = os.path.join(path, "user_mappings.yml") 

    # Create new file if it does not exist yet
    if not os.path.exists(user_mappings_file):
        with open(user_mappings_file, 'w') as fp:
            fp.write('---\n')

    with open(user_mappings_file) as f:
        bugzilla_mapping = yaml.safe_load(f)

    gitlab_users = {}
    if bugzilla_mapping is not None:
        for user in bugzilla_mapping:
            gitlab_username = bugzilla_mapping[user]
            uid = _get_user_id(gitlab_username, gitlab_url, gitlab_headers, verify=verify)
            gitlab_users[gitlab_username] = str(uid)
    else:
        bugzilla_mapping = {}

    mappings = {}
    # bugzilla_username: gitlab_username
    mappings["bugzilla_users"] = bugzilla_mapping

    # gitlab_username: gitlab_userid
    mappings["gitlab_users"] = gitlab_users

    return mappings

def _load_unassign_list(path):
    file = os.path.join(path, "unassign_users") 
    lines = []
    if os.path.exists(file):
        print("Loading unassign list...")
        unassign_file = open(file, "r")
        for line in unassign_file:
            #filter out comments & empty lines
            if not(line.startswith("#")) and len(line.strip()) > 0:
                lines.append(line.strip())
        unassign_file.close()
    temp = {}
    temp["unassign_list"] = lines
    return temp

def _load_milestone_id_cache(project_id, gitlab_url, gitlab_headers, verify):
    """
    Load cache of GitLab milestones and ids
    """
    print("Loading milestone cache...")

    gitlab_milestones = {}
    url = "{}/projects/{}/milestones".format(gitlab_url, project_id)
    result = _perform_request(url, "get", headers=gitlab_headers, verify=verify)
    if result and isinstance(result, list):
        for milestone in result:
            gitlab_milestones[milestone["title"]] = milestone["id"]

    return {"gitlab_milestones": gitlab_milestones}


def _get_user_id(username, gitlab_url, headers, verify):
    url = "{}/users?username={}".format(gitlab_url, username)
    result = _perform_request(url, "get", headers=headers, verify=verify)
    if result and isinstance(result, list):
        return result[0]["id"]
    raise Exception("No gitlab account found for user {}".format(username))


def _load_component_mappings(path):
    with open(os.path.join(path, "component_mappings.yml")) as f:
        component_mappings = yaml.safe_load(f)

    return {"component_mappings": component_mappings}
