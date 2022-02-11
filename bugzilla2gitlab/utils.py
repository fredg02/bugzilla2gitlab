from getpass import getpass

import dateutil.parser
from defusedxml import ElementTree
import pytz
import requests, os, json

from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

SESSION = None

retry_strategy = Retry(
    total=3,
    status_forcelist=[429, 500, 502, 503, 504],
    method_whitelist=["HEAD", "GET", "OPTIONS"]
)
adapter = HTTPAdapter(max_retries=retry_strategy)

def _perform_request(
    url,
    method,
    data={},
    params={},
    headers={},
    files={},
    json=True,
    dry_run=False,
    verify=True,
):
    """
    Utility method to perform an HTTP request.
    """
    if dry_run and method != "get":
        msg = "{} {} dry_run".format(url, method)
        print(msg)
        return 0

    global SESSION
    if not SESSION:
        SESSION = requests.Session()
        SESSION.mount("https://", adapter)
        SESSION.mount("http://", adapter)

    func = getattr(SESSION, method)

    if files:
        result = func(url, files=files, headers=headers, verify=verify)
    else:
        result = func(url, params=params, data=data, headers=headers, verify=verify)

    if result.status_code in [200, 201]:
        if json:
            return result.json()
        return result

    raise Exception(
        "{} failed requests: [{}] Response: [{}] Request data: [{}] Url: [{}] Headers: [{}]".format(
            result.status_code, result.reason, result.content, data, url, headers
        )
    )


def markdown_table_row(key, value):
    """
    Create a row in a markdown table.
    """
    return u"| {} | {} |\n".format(key, value)


def format_datetime(datestr, formatting):
    """
    Apply a datetime format to a string, according to the formatting string.
    """
    parsed_dt = dateutil.parser.parse(datestr)
    return parsed_dt.strftime(formatting)


def format_utc(datestr):
    """
    Convert datetime string to UTC format recognized by GitLab.
    """
    parsed_dt = dateutil.parser.parse(datestr)
    utc_dt = parsed_dt.astimezone(pytz.utc)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

def fetch_bug_list(bugzilla_url, bugzilla_api_token, product, components, status, max_no_of_bugs):
    status_filter = ""
    for s in status:
      status_filter += "&status={}".format(s)
    #print (status_filter)
    components_list = ""
    for component in components:
        component = component.replace("&", "%26") #quickfix to deal with ampersands in component names
        components_list += "&component={}".format(component)

    url = "{}/rest/bug?product={}{}{}&api_key={}".format(bugzilla_url, product, components_list, status_filter, bugzilla_api_token)
    response = _perform_request(url, "get", json=True)
    print ("Found {} bugs for product={}, component={}, status={}".format(len(response["bugs"]), product, components, status))

    # create link to bug list
    status_filter_link = ""
    for s in status:
      status_filter_link += "&bug_status={}".format(s)
    print ("Bug list: {}/buglist.cgi?product={}{}{}".format(bugzilla_url, product, components_list, status_filter_link))

    if len(response["bugs"]) > max_no_of_bugs:
        raise Exception ("Do you really want to import more than {} bugs (consider filtering by bug status!)??".format(max_no_of_bugs))

    buglist = []
    for bug in response["bugs"]:
        buglist.append(bug["id"])
    return buglist

def save_bug_list(buglist, file):
    # dump bug numbers to file
    # Create new file if it does not exist yet
    #TODO: avoid empty line at the end?
    if not os.path.exists(file):
        with open(file, 'w') as fp:
            fp.write('')

    buglist_file = open(file, "w")
    for bug in buglist:
        buglist_file.write("{}\n".format(bug))
    buglist_file.close()

def load_bugzilla_bug(file):
    """
    Read bug XML, return all fields and values in a dictionary.
    """
    bug_fields = {}
    if os.path.exists(file):
        xml_file = open(file, "r")
        bug_fields = parse_bug_fields(xml_file.read())
        xml_file.close()
    else:
        raise Exception ("File {} not found!".format(file))
    return bug_fields

def get_bugzilla_bug(bugzilla_url, bug_id):
    """
    Read bug XML, return all fields and values in a dictionary.
    """
    bug_xml = _fetch_bug_content(bugzilla_url, bug_id)
    return parse_bug_fields(bug_xml)

def parse_bug_fields(bug_xml):
    tree = ElementTree.fromstring(bug_xml)

    bug_fields = {
        "long_desc": [],
        "attachment": [],
        "cc": [],
        "dependson": [],
        "blocked": [],
        "see_also": [],
    }
    for bug in tree:
        for field in bug:
            if field.tag in ("long_desc", "attachment"):
                new = {}
                if field.tag == "attachment":
                    new["isobsolete"] = field.attrib["isobsolete"]
                for data in field:
                    new[data.tag] = data.text
                    if data.tag == "who":
                        new["who_name"] = data.attrib["name"]
                bug_fields[field.tag].append(new)
            elif field.tag == "cc":
                bug_fields[field.tag].append(field.text)
            elif field.tag == "dependson":
                bug_fields[field.tag].append(field.text)
            elif field.tag == "blocked":
                bug_fields[field.tag].append(field.text)
            elif field.tag == "see_also":
                bug_fields[field.tag].append(field.text)
            else:
                bug_fields[field.tag] = field.text
                if field.tag == "reporter":
                    bug_fields["reporter_name"] = field.attrib["name"]

    return bug_fields


def _fetch_bug_content(url, bug_id):
    url = "{}/show_bug.cgi?ctype=xml&id={}".format(url, bug_id)
    response = _perform_request(url, "get", json=False)
    return response.content


def bugzilla_login(url, user, password):
    """
    Log in to Bugzilla as user, asking for password for a few times / until success.
    """
    max_login_attempts = 3
    login_url = "{}/index.cgi".format(url)
    # CSRF protection bypass: GET, then POST
    _perform_request(login_url, "get", json=False)
    for attempt in range(max_login_attempts):
        if password is None:
            bugzilla_password = getpass("Bugzilla password for {}: ".format(user))
        else:
            bugzilla_password = password

        response = _perform_request(
            login_url,
            "post",
            headers={"Referer": login_url},
            data={
                "Bugzilla_login": user,
                "Bugzilla_password": bugzilla_password,
            },
            json=False,
        )
        if response.cookies:
            break
        print("Failed to log in (attempt {})".format(attempt + 1))
    else:
        raise Exception("Failed to log in after {} attempts".format(max_login_attempts))


def get_gitlab_project_id(url, ns_project_name, headers):
    # namespace and project name must be url-encoded! <YOUR-NAMESPACE>%2F<YOUR-PROJECT-NAME>
    ns_project_name = ns_project_name.replace('/','%2F')
    url = "{}/projects/{}".format(url, ns_project_name)
    response = _perform_request(url, "get", json=True, headers=headers)
    return response["id"]


def set_admin_permission(url, id, admin, headers):
    if admin:
        print ("Setting temporary admin permissions for id {}.".format(id))
    else:
        print ("Removing temporary admin permissions for id {}.".format(id))
    # sanitize sudo header!!
    if "sudo" in headers:
        headers.pop("sudo")
    url = "{}/users/{}?admin={}".format(url, id, admin)
    response = _perform_request(url, "put", json=True, headers=headers)
    return response

def is_admin(url, id, headers):
    response = get_gitlab_user(url, id, headers)
    if response.get("is_admin"):
        return response["is_admin"]
    else:
        print ("ERROR: is_admin was not found in response.")

def get_gitlab_user(url, id, headers):
    url = "{}/users/{}".format(url, id)
    response = _perform_request(url, "get", json=True, headers=headers)
    return response

def validate_list(integer_list):
    """
    Ensure that the user-supplied input is a list of integers, or a list of strings
    that can be parsed as integers.
    """
    if not integer_list:
        raise Exception("No bugs to migrate! Call `migrate` with a list of bug ids.")

    if not isinstance(integer_list, list):
        raise Exception(
            "Expected a list of integers. Instead received "
            "a(n) {}".format(type(integer_list))
        )

    for i in integer_list:
        if i:
            try:
                int(i)
            except ValueError:
                raise Exception(
                    "{} is not able to be parsed as an integer, "
                    "and is therefore an invalid bug id.".format(i)
                ) from ValueError

def add_user_mapping(file, bugzilla_user, gitlab_user):
    #TODO: create file, if it does not exist yet?
    user_mappings_file = open(file, "a")
    user_mappings_file.write("{}: {}\n".format(bugzilla_user, gitlab_user))
    user_mappings_file.close()
