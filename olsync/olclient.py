"""Overleaf Client"""
##################################################
# MIT License
##################################################
# File: olclient.py
# Description: Overleaf API Wrapper
# Author: Moritz Glöckl
# License: MIT
# Version: 1.2.0
##################################################

import requests as reqs
from bs4 import BeautifulSoup
import json
import uuid
import time
import re
import zipfile
import io

from tornado.httpclient import HTTPRequest
from tornado.websocket import websocket_connect
  
# The Overleaf Base URL
BASE_URL = "https://www.overleaf.com"

# Where to get the CSRF Token and where to send the login request to
BASE_PROJECT_URL = f"{BASE_URL}/project"
PROJECT_URL = lambda project_id: f"{BASE_PROJECT_URL}/{project_id}"  # The dashboard URL
# The URL to download all the files in zip format
DOWNLOAD_URL = lambda project_id: f"{PROJECT_URL(project_id)}/download/zip"
UPLOAD_URL = lambda project_id: f"{PROJECT_URL(project_id)}/upload"  # The URL to upload files
FOLDER_URL = lambda project_id: f"{PROJECT_URL(project_id)}/folder"  # The URL to create folders
DELETE_URL = lambda project_id, file_type, file_id: f"{PROJECT_URL(project_id)}/{file_type}/{file_id}"  # The URL to delete files
COMPILE_URL = lambda project_id: f"{PROJECT_URL(project_id)}/compile?enable_pdf_caching=true"  # The URL to compile the project
PATH_SEP = "/"  # Use hardcoded path separator for both windows and posix system


class OverleafClient(object):
    """
    Overleaf API Wrapper
    Supports login, querying all projects, querying a specific project, downloading a project and
    uploading a file to a project.
    """

    @staticmethod
    def filter_projects(json_content, more_attrs=None):
        more_attrs = more_attrs or {}
        for p in json_content:
            if not p.get("archived") and not p.get("trashed"):
                if all(p.get(k) == v for k, v in more_attrs.items()):
                    yield p

    def __init__(self, cookie=None, csrf=None):
        self.http_handler = reqs.Session()
        self._cookie = cookie  # Store the cookie for authenticated requests
        self._csrf = csrf  # Store the CSRF token since it is needed for some requests
        self.all_projects = self.get_all_projects()
        self.active_projects = self.get_active_projects()
    
    def safe_get(self, url, *args, **kwargs):
        try:
            response = self.http_handler.get(url, cookies=self._cookie, *args, **kwargs)
            response.raise_for_status()
        except Exception as e:
            raise e
        return response

    def safe_post(self, url, *args, **kwargs):
        try:
            response = self.http_handler.post(url, cookies=self._cookie, *args, **kwargs)
            response.raise_for_status()
        except Exception as e:
            raise e
        return response


    def get_all_projects(self):
        """
        Get all of a user's projects, including archived and trashed
        Returns: List of project objects or None
        """
        project_page = self.safe_get(BASE_PROJECT_URL)
        soup = BeautifulSoup(project_page.content, 'lxml')
        meta = soup.find('meta', {'content': re.compile('\\{.*"projects".*\\}')})
        if meta is not None:
            return json.loads(meta.get('content')).get('projects')
        return None
    
    def get_active_projects(self):
        """
        Get all of a user's active projects (= not archived and not trashed)
        Returns: List of project objects or None
        """
        if self.all_projects is not None:
            filtered_projects = OverleafClient.filter_projects(self.all_projects)
            if filtered_projects is not None:
                return list(filtered_projects)
        return None

    async def get_project(self, project_name):
        """
        Get a specific project by project_name
        Params: project_name, the name of the project
        Returns: project object
        """
        
        if self.all_projects is not None:
            return next(OverleafClient.filter_projects(self.all_projects, {"name": project_name}), None)


    async def download_project(self, project_id, as_zip=True):
        """
        Download project in zip format
        Params: project_id, the id of the project
        Returns: bytes string (zip file)
        """
        r = self.safe_get(DOWNLOAD_URL(project_id), stream=True)
        if as_zip:
            return zipfile.ZipFile(io.BytesIO(r.content))
        return r.content

    def create_folder(self, project_id, parent_folder_id, folder_name):
        """
        Create a new folder in a project

        Params:
        project_id: the id of the project
        parent_folder_id: the id of the parent folder, root is the project_id
        folder_name: how the folder will be named

        Returns: folder id or None
        """

        params = {
            "parent_folder_id": parent_folder_id,
            "name": folder_name
        }
        headers = {
            "X-Csrf-Token": self._csrf
        }
        r = self.safe_post(FOLDER_URL(project_id), headers=headers, json=params)

        if r.ok:
            return json.loads(r.content)
        elif r.status_code == str(400):
            # Folder already exists
            return
        else:
            raise reqs.HTTPError()

    # Implementation HEAVILY based off of https://github.com/da-h/AirLatex.vim/
    async def get_project_infos(self, project_id):
        """
        Get detailed project infos about the project

        Params:
        project_id: the id of the project

        Returns: project details
        """

        cookie = f"GCLB={self._cookie['GCLB']}; overleaf_session2={self._cookie['overleaf_session2']}"
        codere = re.compile(r"(\d):(?:(\d+)(\+?))?:(?::(?:(\d+)(\+?))?(.*))?")
        requests = {}

        async def getWebSocketURL():
            channel_info = self.safe_get(
                f"{BASE_URL}/socket.io/1/?projectId={project_id}&t={int(time.time())}",
                headers={'Cookie': cookie}
            )
            ws_channel = channel_info.text[0:channel_info.text.find(':')]
            web_socket_url = f'{BASE_URL}/socket.io/1/websocket/{ws_channel}?projectId={project_id}'.replace('http', 'ws')
            print(f'\n{web_socket_url}')
            return web_socket_url

        async def connect():
            request = HTTPRequest(await getWebSocketURL(), headers={'Cookie': cookie})
            return await websocket_connect(request)
        
        async def run():
            project_infos = None
            try:
                ws = await connect()
                while project_infos is None:
                    response = await ws.read_message()

                    print(f'\nRaw response: {response}')

                    if response is None:
                        break

                    code, await_id, await_mult, answer_id, answer_mult, data = codere.match(response).groups()

                    if data:
                        try:
                            data = json.loads(data)
                        except:
                            data = {"name":"error"}

                    # error occured
                    if code == "0":
                        break

                    # first message
                    elif code == "1":
                        pass

                    # keep alive
                    elif code == "2":
                        await keep_alive()

                    # server request
                    elif code == "5":
                        if not isinstance(data, dict):
                            pass

                        # connection accepted => join Project
                        if data["name"] == "connectionAccepted":
                            await join_project()

                        elif data["name"] == "joinProjectResponse":
                            project_infos = data["args"]

                        # unknown message
                        else:
                            raise Exception(f"Unknown server request: {data}\nResponse: {response}")

                    # answer to our request
                    elif code == "6":

                        # get request command
                        request = requests[answer_id]
                        cmd = request["name"]

                        # joinProject => server lists project information
                        if cmd == "joinProject":
                            project_infos = data[1]

                        # unknown answer
                        else:
                            raise Exception(f"Server responded to unknown request: {cmd}\nResponse: {response}")

                    # Unauthorised
                    elif code == "7":
                        raise Exception(f"Error: Unauthorized, refresh Presistent Overleaf cookies by logging in again.\nResponse: {response}")

                    # unknown message
                    else:
                        raise Exception(f"Server responsded with unknown code: {code}\nResponse: {response}")

            except Exception as e:
                raise e

            return project_infos

        project_infos = await run()

        return project_infos

    def upload_file(self, project_id, project_infos, file_name, file_size, file):
        """
        Upload a file to the project

        Params:
        project_id: the id of the project
        file_name: how the file will be named
        file_size: the size of the file in bytes
        file: the file itself

        Returns: True on success, False on fail
        """

        # Set the folder_id to the id of the root folder
        folder_id = project_infos['rootFolder'][0]['_id']

        # The file name contains path separators, check folders
        if PATH_SEP in file_name:
            local_folders = file_name.split(PATH_SEP)[:-1]  # Remove last item since this is the file name
            current_overleaf_folder = project_infos['rootFolder'][0]['folders']  # Set the current remote folder

            for local_folder in local_folders:
                exists_on_remote = False
                for remote_folder in current_overleaf_folder:
                    # Check if the folder exists on remote, continue with the new folder structure
                    if local_folder.lower() == remote_folder['name'].lower():
                        exists_on_remote = True
                        folder_id = remote_folder['_id']
                        current_overleaf_folder = remote_folder['folders']
                        break
                # Create the folder if it doesn't exist
                if not exists_on_remote:
                    new_folder = self.create_folder(project_id, folder_id, local_folder)
                    current_overleaf_folder.append(new_folder)
                    folder_id = new_folder['_id']
                    current_overleaf_folder = new_folder['folders']
        params = {
            "folder_id": folder_id,
            "_csrf": self._csrf,
            "qquuid": str(uuid.uuid4()),
            "qqfilename": file_name,
            "qqtotalfilesize": file_size,
        }
        files = {
            "qqfile": file
        }

        # Upload the file to the predefined folder
        r = self.safe_post(UPLOAD_URL(project_id), params=params, files=files)

        return r.status_code == str(200) and json.loads(r.content)["success"]

    def delete_file(self, project_id, project_infos, file_name):
        """
        Deletes a project's file

        Params:
        project_id: the id of the project
        file_name: how the file will be named

        Returns: True on success, False on fail
        """

        file = None

        # The file name contains path separators, check folders
        if PATH_SEP in file_name:
            local_folders = file_name.split(PATH_SEP)[:-1]  # Remove last item since this is the file name
            current_overleaf_folder = project_infos['rootFolder'][0]['folders']  # Set the current remote folder

            for local_folder in local_folders:
                for remote_folder in current_overleaf_folder:
                    if local_folder.lower() == remote_folder['name'].lower():
                        file = next((v for v in remote_folder['docs'] if v['name'] == file_name.split(PATH_SEP)[-1]),
                                    None)
                        current_overleaf_folder = remote_folder['folders']
                        break
        # File is in root folder
        else:
            file = next((v for v in project_infos['rootFolder'][0]['docs'] if v['name'] == file_name), None)

        # File not found!
        if file is None:
            return False

        headers = {
            "X-Csrf-Token": self._csrf
        }

        r = reqs.delete(DELETE_URL(project_id, 'doc', file['_id']), cookies=self._cookie, headers=headers, json={})

        return r.status_code == str(204)

    def download_pdf(self, project_id):
        """
        Compiles and returns a project's PDF

        Params:
        project_id: the id of the project

        Returns: PDF file name and content on success
        """
        headers = {
            "X-Csrf-Token": self._csrf
        }

        body = {
            "check": "silent",
            "draft": False,
            "incrementalCompilesEnabled": True,
            "rootDoc_id": "",
            "stopOnFirstError": False
        }

        r = self.safe_post(COMPILE_URL(project_id), headers=headers, json=body)

        if not r.ok:
            raise reqs.HTTPError()

        compile_result = json.loads(r.content)

        if compile_result["status"] != "success":
            raise reqs.HTTPError()

        pdf_file = next(v for v in compile_result['outputFiles'] if v['type'] == 'pdf')

        download_req = self.safe_get(BASE_URL + pdf_file['url'], headers=headers)

        if download_req.ok:
            return pdf_file['path'], download_req.content

        return None
