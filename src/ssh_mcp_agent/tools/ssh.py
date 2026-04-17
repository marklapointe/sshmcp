import paramiko
import os
from typing import Optional, Tuple
from dataclasses import dataclass

@dataclass
class SSHConfig:
    host: str
    username: str
    password: Optional[str] = None
    key_filename: Optional[str] = None
    port: int = 22

class SSHClient:
    """
    A utility class to handle SSH operations using Paramiko.
    """
    def __init__(self, config: SSHConfig):
        self.config = config
        self._client: Optional[paramiko.SSHClient] = None

    def _get_client(self) -> paramiko.SSHClient:
        if self._client is not None:
            return self._client
        
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        connect_kwargs = {
            "hostname": self.config.host,
            "port": self.config.port,
            "username": self.config.username,
            "timeout": 10,
        }
        
        if self.config.password:
            connect_kwargs["password"] = self.config.password
        if self.config.key_filename:
            connect_kwargs["key_filename"] = self.config.key_filename
            
        client.connect(**connect_kwargs)
        self._client = client
        return client

    def execute_command(self, command: str) -> Tuple[int, str, str]:
        """
        Executes a command on the remote system.
        Returns: (exit_status, stdout, stderr)
        """
        client = self._get_client()
        stdin, stdout, stderr = client.exec_command(command)
        exit_status = stdout.channel.recv_exit_status()
        return exit_status, stdout.read().decode('utf-8'), stderr.read().decode('utf-8')

    def upload_file(self, local_path: str, remote_path: str):
        """
        Uploads a file to the remote system using SFTP.
        """
        client = self._get_client()
        sftp = client.open_sftp()
        try:
            sftp.put(local_path, remote_path)
        finally:
            sftp.close()

    def download_file(self, remote_path: str, local_path: str):
        """
        Downloads a file from the remote system using SFTP.
        """
        client = self._get_client()
        sftp = client.open_sftp()
        try:
            sftp.get(remote_path, local_path)
        finally:
            sftp.close()

    def close(self):
        if self._client:
            self._client.close()
            self._client = None
