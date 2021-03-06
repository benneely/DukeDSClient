"""DataServiceApi - communicates with to Duke Data Service REST API."""
import json
import requests
import time
from ddsc.config import LOCAL_CONFIG_FILENAME

AUTH_TOKEN_CLOCK_SKEW_MAX = 5 * 60  # 5 minutes
SETUP_GUIDE_URL = "https://github.com/Duke-GCB/DukeDSClient/blob/master/docs/GettingAgentAndUserKeys.md"

MISSING_INITIAL_SETUP_MSG = """Missing initial setup.
You need to add agent_key and user_key to {}.
Follow this guide: {}\n""".format(LOCAL_CONFIG_FILENAME, SETUP_GUIDE_URL)

SOFTWARE_AGENT_NOT_FOUND_MSG = """Your software agent was not found on the server.
Perhaps you have the wrong URL. You can change it via the 'url' setting in {}.""".format(LOCAL_CONFIG_FILENAME, LOCAL_CONFIG_FILENAME)

UNEXPECTED_PAGING_DATA_RECEIVED = """Received unexpected paging data in single item response.
This may be due to an incompatible DukeDS API change.
Try upgrading ddsclient: pip install --upgrade DukeDSClient
"""

DEFAULT_RESULTS_PER_PAGE = 100

requests_session = requests.Session()

class ContentType(object):
    """
    Contains the types of content for use with http headers.
    """
    json = 'application/json'
    form = 'application/x-www-form-urlencoded'


class DataServiceAuth(object):
    """
    Handles authorization refreshing for DataServiceApi.
    """
    def __init__(self, config):
        """
        Setup with initial authorization settings from config.
        :param config: ddsc.config.Config settings such as auth, user_key, agent_key
        """
        self.config = config
        self._auth = self.config.auth
        self._expires = None

    def get_auth(self):
        """
        Gets an active token refreshing it if necessary.
        :return: str valid active authentication token.
        """
        if self.legacy_auth():
            return self._auth
        if not self.auth_expired():
            return self._auth
        self.claim_new_token()
        return self._auth

    def claim_new_token(self):
        """
        Update internal state to have a new token using a no authorization data service.
        """
        # Intentionally doing this manually so we don't have a chicken and egg problem with DataServiceApi.
        headers = {
            'Content-Type': ContentType.json,
        }
        data = {
            "agent_key": self.config.agent_key,
            "user_key": self.config.user_key,
        }
        url = self.config.url + "/software_agents/api_token"
        response = requests.post(url, headers=headers, data=json.dumps(data))
        if response.status_code == 404:
            if not self.config.agent_key:
                raise ValueError(MISSING_INITIAL_SETUP_MSG)
            else:
                raise ValueError(SOFTWARE_AGENT_NOT_FOUND_MSG)
        if response.status_code != 201:
            msg_format = 'Failed to create auth token status:{}\n{}'
            raise ValueError(msg_format.format(response.status_code, response.text))
        resp_json = response.json()
        self._auth = resp_json['api_token']
        self._expires = resp_json['expires_on']

    def get_auth_data(self):
        """
        Returns a tuple that can be build to recreate this object's state.
        """
        return self._auth, self._expires

    def set_auth_data(self, auth_expires_tuple):
        """
        Recreates setup based on tuple returned by get_auth_data.
        :param auth_expires_tuple (auth,expires) values returned by call to get_auth_data()
        """
        self._auth = auth_expires_tuple[0]
        self._expires = auth_expires_tuple[1]

    def legacy_auth(self):
        """
        Has user specified a single auth token to use with an unknown expiration.
        This is the old method. User should update their config file.
        :return: boolean true if we should never try to fetch a token
        """
        return self._auth and not self._expires

    def auth_expired(self):
        """
        Compare the expiration value of our current token including a CLOCK_SKEW.
        :return: true if the token has expired
        """
        if self._auth and self._expires:
            now_with_skew = time.time() + AUTH_TOKEN_CLOCK_SKEW_MAX
            return now_with_skew > self._expires
        return True


class DataServiceError(Exception):
    """
    Error that wraps up info about it and creates an informative string.
    """
    def __init__(self, response, url_suffix, request_data):
        """
        Create exception for failed response.
        :param response: requests.Response response that was in error
        :param url_suffix: str url we were trying to connect to
        :param request_data: object data we were sending to url
        """
        resp_json = None
        try:
            resp_json = response.json()
        except:
            resp_json = {}
        if response.status_code == 500:
            if resp_json and not resp_json.get('reason'):
                resp_json = {'reason':'Internal Server Error', 'suggestion':'Contact DDS support.'}
        Exception.__init__(self,'Error {} on {} Reason:{} Suggestion:{}'.format(
            response.status_code, url_suffix, resp_json.get('reason',resp_json.get('error','')), resp_json.get('suggestion','')
        ))
        self.response = resp_json
        self.url_suffix = url_suffix
        self.request_data = request_data
        self.status_code = response.status_code


class DataServiceApi(object):
    """
    Sends json messages and receives responses back from Duke Data Service api.
    See https://github.com/Duke-Translational-Bioinformatics/duke-data-service.
    """
    def __init__(self, auth, url, http=requests):
        """
        Setup for REST api.
        :param auth: str auth token to be send via Authorization header
        :param url: str root url of the data service
        :param http: object requests style http object to do get/post/put
        """
        self.auth = auth
        self.base_url = url
        self.http = http

    def _url_parts(self, url_suffix, data, content_type):
        """
        Format the url data based on config_type.
        :param url_suffix: str URL path we are sending a GET/POST/PUT to
        :param data: object data we are sending
        :param content_type: str from ContentType that determines how we format the data
        :return: complete url, formatted data, and headers for sending
        """
        url = self.base_url + url_suffix
        send_data = data
        if content_type == ContentType.json:
            send_data = json.dumps(data)
        headers = {
            'Content-Type': content_type,
        }
        if self.auth:
            headers['Authorization'] = self.auth.get_auth()
        return url, send_data, headers

    def _post(self, url_suffix, data, content_type=ContentType.json):
        """
        Send POST request to API at url_suffix with post_data.
        Raises error if x-total-pages is contained in the response.
        :param url_suffix: str URL path we are sending a POST to
        :param data: object data we are sending
        :param content_type: str from ContentType that determines how we format the data
        :return: requests.Response containing the result
        """
        (url, data_str, headers) = self._url_parts(url_suffix, data, content_type=content_type)
        resp = self.http.post(url, data_str, headers=headers)
        return self._check_err(resp, url_suffix, data, allow_pagination=False)

    def _put(self, url_suffix, data, content_type=ContentType.json):
        """
        Send PUT request to API at url_suffix with post_data.
        Raises error if x-total-pages is contained in the response.
        :param url_suffix: str URL path we are sending a PUT to
        :param data: object data we are sending
        :param content_type: str from ContentType that determines how we format the data
        :return: requests.Response containing the result
        """
        (url, data_str, headers) = self._url_parts(url_suffix, data, content_type=content_type)
        resp = self.http.put(url, data_str, headers=headers)
        return self._check_err(resp, url_suffix, data, allow_pagination=False)

    def _get_single_item(self, url_suffix, data, content_type=ContentType.json):
        """
        Send GET request to API at url_suffix with post_data.
        Raises error if x-total-pages is contained in the response.
        :param url_suffix: str URL path we are sending a GET to
        :param url_data: object data we are sending
        :param content_type: str from ContentType that determines how we format the data
        :return: requests.Response containing the result
        """
        (url, data_str, headers) = self._url_parts(url_suffix, data, content_type=content_type)
        resp = self.http.get(url, headers=headers, params=data_str)
        return self._check_err(resp, url_suffix, data, allow_pagination=False)

    def _get_single_page(self, url_suffix, data, content_type, page_num):
        """
        Send GET request to API at url_suffix with post_data adding page and per_page parameters to
        retrieve a single page. Always requests with per_page=DEFAULT_RESULTS_PER_PAGE.
        :param url_suffix: str URL path we are sending a GET to
        :param data: object data we are sending
        :param content_type: str from ContentType that determines how we format the data
        :param page_num: int: page number to fetch
        :return: requests.Response containing the result
        """
        data_with_per_page = dict(data)
        data_with_per_page['page'] = page_num
        data_with_per_page['per_page'] = DEFAULT_RESULTS_PER_PAGE
        (url, data_str, headers) = self._url_parts(url_suffix, data_with_per_page, content_type=content_type)
        resp = self.http.get(url, headers=headers, params=data_str)
        return self._check_err(resp, url_suffix, data, allow_pagination=True)

    def _get_collection(self, url_suffix, data, content_type=ContentType.json):
        """
        Performs GET for all pages based on x-total-pages in first response headers.
        Merges the json() 'results' arrays.
        If x-total-pages is missing or 1 just returns the response without fetching multiple pages.
        :param url_suffix: str URL path we are sending a GET to
        :param data: object data we are sending
        :param content_type: str from ContentType that determines how we format the data
        :return: requests.Response containing the result
        """
        response = self._get_single_page(url_suffix, data, content_type, page_num=1)
        total_pages_str = response.headers.get('x-total-pages')
        if total_pages_str:
            total_pages = int(total_pages_str)
            if total_pages > 1:
                multi_response = MultiJSONResponse(base_response=response, merge_array_field_name="results")
                for page in range(2, total_pages + 1):
                    additional_response = self._get_single_page(url_suffix, data, content_type, page_num=page)
                    multi_response.add_response(additional_response)
                return multi_response
        return response

    def _delete(self, url_suffix, data, content_type=ContentType.json):
        """
        Send DELETE request to API at url_suffix with post_data.
        Raises error if x-total-pages is contained in the response.
        :param url_suffix: str URL path we are sending a DELETE to
        :param data: object data we are sending
        :param content_type: str from ContentType that determines how we format the data
        :return: requests.Response containing the result
        """
        (url, data_str, headers) = self._url_parts(url_suffix, data, content_type=content_type)
        resp = self.http.delete(url, headers=headers, params=data_str)
        return self._check_err(resp, url_suffix, data, allow_pagination=False)

    @staticmethod
    def _check_err(resp, url_suffix, data, allow_pagination):
        """
        Raise DataServiceError if the response wasn't successful.
        :param resp: requests.Response back from the request
        :param url_suffix: str url to include in an error message
        :param data: data payload we sent
        :param allow_pagination: when False and response headers contains 'x-total-pages' raises an error.
        :return: requests.Response containing the successful result
        """
        total_pages = resp.headers.get('x-total-pages')
        if not allow_pagination and total_pages:
            raise ValueError(UNEXPECTED_PAGING_DATA_RECEIVED)
        if 200 <= resp.status_code < 300:
           return resp
        raise DataServiceError(resp, url_suffix, data)

    def create_project(self, project_name, desc):
        """
        Send POST to /projects creating a new project with the specified name and desc.
        Raises DataServiceError on error.
        :param project_name: str name of the project
        :param desc: str description of the project
        :return: requests.Response containing the successful result
        """
        data = {
            "name": project_name,
            "description": desc
        }
        return self._post("/projects", data)

    def get_projects(self):
        """
        Send GET to /projects returning a list of all projects for the current user.
        Raises DataServiceError on error.
        :return: requests.Response containing the successful result
        """
        return self._get_collection("/projects", {})

    def get_project_by_id(self, id):
        """
        Send GET request to /projects/{id} to get project details
        :param id: str uuid of the project
        :return: requests.Response containing the successful result
        """
        return self._get_single_item('/projects/{}'.format(id), {})

    def get_file_url(self, file_id):
        """
        Send GET to /files/{}/url returning a url to download the file.
        Raises DataServiceError on error.
        :param file_id: str uuid of the file we want to download
        :return: requests.Response containing the successful result
        """
        return self._get_single_item("/files/{}/url".format(file_id), {})

    def create_folder(self, folder_name, parent_kind_str, parent_uuid):
        """
        Send POST to /folders to create a new folder with specified name and parent.
        :param folder_name: str name of the new folder
        :param parent_kind_str: str type of parent folder has(dds-folder,dds-project)
        :param parent_uuid: str uuid of the parent object
        :return: requests.Response containing the successful result
        """
        data = {
            'name': folder_name,
            'parent': {
                'kind': parent_kind_str,
                'id': parent_uuid
            }
        }
        return self._post("/folders", data)

    def get_project_children(self, project_id, name_contains):
        """
        Send GET to /projects/{project_id} filtering by a name.
        :param project_id: str uuid of the project
        :param name_contains: str name to filter folders by (if not None this method works recursively)
        :return: requests.Response containing the successful result
        """
        return self._get_children('projects', project_id, name_contains)

    def get_folder_children(self, folder_id, name_contains):
        """
        Send GET to /folders/{folder_id} filtering by a name.
        :param folder_id: str uuid of the folder
        :param name_contains: str name to filter children by (if not None this method works recursively)
        :return: requests.Response containing the successful result
        """
        return self._get_children('folders', folder_id, name_contains)

    def _get_children(self, parent_name, parent_id, name_contains):
        """
        Send GET message to /<parent_name>/<parent_id>/children to fetch info about children(files and folders)
        :param parent_name: str 'projects' or 'folders'
        :param parent_id: str uuid of project or folder
        :param name_contains: name filtering (if not None this method works recursively)
        :return: requests.Response containing the successful result
        """
        data = {}
        if not name_contains is None:
            data['name_contains'] = name_contains
        url_prefix = "/{}/{}/children".format(parent_name, parent_id)
        return self._get_collection(url_prefix, data, content_type=ContentType.form)

    def create_upload(self, project_id, filename, content_type, size,
                      hash_value, hash_alg):
        """
        Post to /projects/{project_id}/uploads to create a uuid for uploading chunks.
        NOTE: The optional hash_value and hash_alg parameters are being removed from the DukeDS API.
        :param project_id: str uuid of the project we are uploading data for.
        :param filename: str name of the file we want to upload
        :param content_type: str mime type of the file
        :param size: int size of the file in bytes
        :param hash_value: str hash value of the entire file
        :param hash_alg: str algorithm used to create hash_value
        :return: requests.Response containing the successful result
        """
        data = {
            "name": filename,
            "content_type": content_type,
            "size": size,
            "hash": {
                "value": hash_value,
                "algorithm": hash_alg
            }
        }
        return self._post("/projects/" + project_id + "/uploads", data)

    def create_upload_url(self, upload_id, number, size, hash_value, hash_alg):
        """
        Given an upload created by create_upload retrieve a url where we can upload a chunk.
        :param upload_id: uuid of the upload
        :param number: int incrementing number of the upload
        :param size: int size of the chunk in bytes
        :param hash_value: str hash value of chunk
        :param hash_alg: str algorithm used to create hash
        :return: requests.Response containing the successful result
        """
        data = {
            "number": number,
            "size": size,
            "hash": {
                "value": hash_value,
                "algorithm": hash_alg
            }
        }
        return self._put("/uploads/" + upload_id + "/chunks", data)

    def complete_upload(self, upload_id, hash_value, hash_alg):
        """
        Mark the upload we created in create_upload complete.
        :param upload_id: str uuid of the upload to complete.
        :param hash_value: str hash value of chunk
        :param hash_alg: str algorithm used to create hash
        :return: requests.Response containing the successful result
        """
        data = {
            "hash[value]": hash_value,
            "hash[algorithm]": hash_alg
        }
        return self._put("/uploads/" + upload_id + "/complete", data, content_type=ContentType.form)

    def create_file(self, parent_kind, parent_id, upload_id):
        """
        Create a new file after completing an upload.
        :param parent_kind: str kind of parent(dds-folder,dds-project)
        :param parent_id: str uuid of parent
        :param upload_id: str uuid of complete upload
        :return: requests.Response containing the successful result
        """
        data = {
            "parent": {
                "kind": parent_kind,
                "id": parent_id
            },
            "upload": {
                "id": upload_id
            }
        }
        return self._post("/files/", data)

    def update_file(self, file_id, upload_id):
        """
        Send PUT request to /files/{file_id} to update the file contents to upload_id and sets a label.
        :param file_id: str uuid of file
        :param upload_id: str uuid of the upload where all the file chunks where uploaded
        :param label: str short display label for the file
        :return: requests.Response containing the successful result
        """
        put_data = {
            "upload[id]": upload_id,
        }
        return self._put("/files/" + file_id, put_data, content_type=ContentType.form)

    def send_external(self, http_verb, host, url, http_headers, chunk):
        """
        Used with create_upload_url to send a chunk the the possibly external object store.
        :param http_verb: str PUT or POST
        :param host: str host we are sending the chunk to
        :param url: str url to use when sending
        :param http_headers: object headers to send with the request
        :param chunk: content to send
        :return: requests.Response containing the successful result
        """
        if http_verb == 'PUT':
            return requests_session.put(host + url, data=chunk, headers=http_headers)
        elif http_verb == 'POST':
            return requests_session.post(host + url, data=chunk, headers=http_headers)
        else:
            raise ValueError("Unsupported http_verb:" + http_verb)

    def receive_external(self, http_verb, host, url, http_headers):
        """
        Retrieve a streaming request for a file.
        :param http_verb: str GET is only supported right now
        :param host: str host we are requesting the file from
        :param url: str url to ask the host for
        :param http_headers: object headers to send with the request
        :return: requests.Response containing the successful result
        """
        if http_verb == 'GET':
            return requests_session.get(host + url, headers=http_headers, stream=True)
        else:
            raise ValueError("Unsupported http_verb:" + http_verb)

    def get_users_by_full_name(self, full_name):
        """
        Send GET request to /users filtering by those full name contains full_name.
        :param full_name: str name of the user we are searching for
        :return: requests.Response containing the successful result
        """
        data = {
            "full_name_contains": full_name,
        }
        return self._get_collection('/users', data, content_type=ContentType.form)

    def get_all_users(self):
        """
        Send GET request to /users for all users.
        :return: requests.Response containing the successful result
        """
        data = {}
        return self._get_collection('/users', data, content_type=ContentType.form)

    def get_user_by_id(self, id):
        """
        Send GET request to /users/{id} to get user details
        :param id: str uuid of the user
        :return: requests.Response containing the successful result
        """
        return self._get_single_item('/users/{}'.format(id), {})

    def set_user_project_permission(self, project_id, user_id, auth_role):
        """
        Send PUT request to /projects/{project_id}/permissions/{user_id/ with auth_role value.
        :param project_id: str uuid of the project
        :param user_id: str uuid of the user
        :param auth_role: str project role eg 'project_admin'
        :return: requests.Response containing the successful result
        """
        put_data = {
            "auth_role[id]": auth_role
        }
        return self._put("/projects/" + project_id + "/permissions/" + user_id, put_data,
                         content_type=ContentType.form)

    def get_user_project_permission(self, project_id, user_id):
        """
        Send GET request to /projects/{project_id}/permissions/{user_id/.
        :param project_id: str uuid of the project
        :param user_id: str uuid of the user
        :param auth_role: str project role eg 'project_admin'
        :return: requests.Response containing the successful result
        """
        return self._get_single_item("/projects/" + project_id + "/permissions/" + user_id, {})

    def revoke_user_project_permission(self, project_id, user_id):
        """
        Send DELETE request to /projects/{project_id}/permissions/{user_id so they will no longer have permissions.
        :param project_id: str uuid of the project
        :param user_id: str uuid of the user
        :param auth_role: str project role eg 'project_admin'
        :return: requests.Response containing the successful result
        """
        return self._delete("/projects/" + project_id + "/permissions/" + user_id, {})

    def get_file(self, file_id):
        """
        Send GET request to /files/{file_id} to retrieve file info.
        :param file_id: str uuid of the file we want info about
        :return: requests.Response containing the successful result
        """
        return self._get_single_item('/files/' + file_id, {})

    def get_api_token(self, agent_key, user_key):
        """
        Send POST request to get an auth token.
        This method doesn't require auth obviously.
        :param agent_key: str agent key (who is acting on behalf of the user)
        :param user_key: str secret user key
        :return: requests.Response containing the successful result
        """
        data = {
            "agent_key": agent_key,
            "user_key": user_key,
        }
        return self._post("/software_agents/api_token", data)

    def get_current_user(self):
        """
        Send GET request to get info about current user.
        :return: requests.Response containing the successful result
        """
        return self._get_single_item("/current_user", {})

    def delete_project(self, project_id):
        """
        Send DELETE request to the url for this project.
        :param project_id: str uuid of the project
        :return: requests.Response containing the successful result
        """
        return self._delete("/projects/" + project_id, {})

    def get_auth_roles(self, context):
        """
        Send GET request to get list of auth_roles for a context.
        :param context: str which roles do we want 'project' or 'system'
        :return: requests.Response containing the successful result
        """
        return self._get_all_pages("/auth_roles", {"context": context}, content_type=ContentType.form)

    def get_project_transfers(self, project_id):
        """
        Send GET request to get list of transfers for a project
        :param project_id: str uuid of the project
        :return: requests.Response containing the successful result
        """
        return self._get_all_pages("/projects/" + project_id + "/transfers", {})

    def create_project_transfer(self, project_id, to_user_ids):
        """
        Send POST request to initiate transfer of a project to the specified user ids
        :param project_id: str uuid of the project
        :param to_users: list of user uuids to receive the project
        :return: requests.Response containing the successful result
        """
        data = {
            "to_users[][id]": to_user_ids,
        }
        return self._post("/projects/" + project_id + "/transfers", data,
                          content_type=ContentType.form)

    def get_project_transfer(self, transfer_id):
        """
        Send GET request to single project_transfer by id
        :param transfer_id: str uuid of the project_transfer
        :return: requests.Response containing the successful result
        """
        return self._get_single_item("/project_transfers/" + transfer_id, {})

    def _process_project_transfer(self, action, transfer_id, status_comment):
        """
        Send PUT request to one of the project transfer action endpoints
        :param action: str name of the action (reject/accept/cancel)
        :param transfer_id: str uuid of the project_transfer
        :param status_comment: str comment about the action, optional
        :return: requests.Response containing the successful result
        """
        data = {}
        if status_comment:
            data["status_comment"] = status_comment
        path = "/project_transfers/{}/{}".format(transfer_id, action)
        return self._put(path, data, content_type=ContentType.form)

    def reject_project_transfer(self, transfer_id, status_comment=None):
        """
        Send PUT request to reject a project_transfer by id
        :param transfer_id: str uuid of the project_transfer
        :param status_comment: str comment or reason for rejecting or None
        :return: requests.Response containing the successful result
        """
        return self._process_project_transfer('reject', transfer_id, status_comment)

    def cancel_project_transfer(self, transfer_id, status_comment=None):
        """
        Send PUT request to cancel a project_transfer by id
        :param transfer_id: str uuid of the project_transfer
        :param status_comment: str comment or reason for cancelling or None
        :return: requests.Response containing the successful result
        """
        return self._process_project_transfer('cancel', transfer_id, status_comment)

    def accept_project_transfer(self, transfer_id, status_comment=None):
        """
        Send PUT request to accept a project_transfer by id
        :param transfer_id: str uuid of the project_transfer
        :param status_comment: str comment or reason for accepting
        :return: requests.Response containing the successful result
        """
        return self._process_project_transfer('accept', transfer_id, status_comment)
        
    def get_activities(self):
        """
        Send GET to /activities returning a list of all provenance activities
        for the current user. Raises DataServiceError on error.
        :return: requests.Response containing the successful result
        """
        return self._get_collection("/activities", {})
        
    def create_activity(self, activity_name, desc=None, started_on=None, ended_on=None):
        """
        Send POST to /activities creating a new activity with the specified name and desc.
        Raises DataServiceError on error.
        :param activity_name: str name of the activity
        :param desc: str description of the activity (optional)
        :param started_on: str datetime when the activity started (optional)
        :param ended_on: str datetime when the activity ended (optional)
        :return: requests.Response containing the successful result
        """
        data = {
            "name": activity_name,
            "description": desc,
            "started_on": started_on,
            "ended_on": ended_on
        }
        return self._post("/activities", data)
        
    def delete_activity(self, activity_id):
        """
        Send DELETE request to the url for this activity.
        :param activity_id: str uuid of the activity
        :return: requests.Response containing the successful result
        """
        return self._delete("/activities/" + activity_id, {})
        
    def get_activity_by_id(self, activity_id):
        """
        Send GET request to /activities/{id} to get activity details
        :param id: str uuid of the activity
        :return: requests.Response containing the successful result
        """
        return self._get_single_item('/activities/{}'.format(activity_id), {})
        
    def update_activity(self, activity_id, activity_name, desc=None, 
                        started_on=None, ended_on=None):
        """
        Send PUT request to /activities/{activity_id} to update the activity metadata.
        Raises ValueError if at least one field is not updated.
        :param activity_id: str uuid of activity
        :param activity_name: str new name of the activity
        :param desc: str description of the activity (optional)
        :param started_on: str date the updated activity began on (optional)
        :param ended_on: str date the updated activity ended on (optional)
        :return: requests.Response containing the successful result
        """
        put_data = {
            "name": activity_name,
            "description": desc,
            "started_on": started_on,
            "ended_on": ended_on
        }
        return self._put("/activities/" + activity_id, put_data)


class MultiJSONResponse(object):
    """
    Wraps up multiple requests.Response objects into an object that will return composite dictionary for json() method.
    """
    def __init__(self, base_response, merge_array_field_name):
        """
        Setup response with primary response that will answer all methods/properties except json()
        :param base_response: requests.Response containing the successful result that will respond methods/properties
        :param merge_fieldname: str: name of the array field in the JSON data to merge when add_response is called
        """
        self.base_response = base_response
        self.merge_array_field_name = merge_array_field_name
        self.combined_json = self.base_response.json()

    def __getattr__(self, attr):
        """
        Forwards all calls to base_response property
        """
        return getattr(self.base_response, attr)

    def json(self):
        """
        Returns json created by merging the base response's json() merge_array_field_name value
        :return: dict: combined dictionary from multiple responses
        """
        return self.combined_json

    def add_response(self, response):
        """
        Add data from json() to data returned by json()
        :param response: requests.Response containing the successful JSON result to be merged
        """
        key = self.merge_array_field_name
        response_json = response.json()
        value = self.combined_json[key]
        self.combined_json[self.merge_array_field_name] = value + response_json[key]
