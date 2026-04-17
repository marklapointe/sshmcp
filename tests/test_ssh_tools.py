import pytest
from unittest.mock import MagicMock, patch
from ssh_mcp_agent.tools.ssh import SSHClient, SSHConfig

def test_ssh_config():
    config = SSHConfig(host="localhost", username="user", password="pass")
    assert config.host == "localhost"
    assert config.username == "user"
    assert config.password == "pass"
    assert config.port == 22

@patch("paramiko.SSHClient")
def test_ssh_execute_command(mock_paramiko_cls):
    mock_client = mock_paramiko_cls.return_value
    mock_stdout = MagicMock()
    mock_stdout.read.return_value = b"output"
    mock_stdout.channel.recv_exit_status.return_value = 0
    mock_stderr = MagicMock()
    mock_stderr.read.return_value = b""
    mock_client.exec_command.return_value = (None, mock_stdout, mock_stderr)
    
    config = SSHConfig(host="localhost", username="user", password="pass")
    client = SSHClient(config)
    
    status, stdout, stderr = client.execute_command("ls")
    
    assert status == 0
    assert stdout == "output"
    assert stderr == ""
    mock_client.connect.assert_called_once()
    
    # Test caching of client
    client.execute_command("whoami")
    assert mock_client.connect.call_count == 1

@patch("paramiko.SSHClient")
def test_ssh_upload_file(mock_paramiko_cls):
    mock_client = mock_paramiko_cls.return_value
    mock_sftp = MagicMock()
    mock_client.open_sftp.return_value = mock_sftp
    
    config = SSHConfig(host="localhost", username="user", key_filename="key.pem")
    client = SSHClient(config)
    client.upload_file("local.txt", "remote.txt")
    
    mock_sftp.put.assert_called_once_with("local.txt", "remote.txt")
    mock_sftp.close.assert_called_once()

@patch("paramiko.SSHClient")
def test_ssh_download_file(mock_paramiko_cls):
    mock_client = mock_paramiko_cls.return_value
    mock_sftp = MagicMock()
    mock_client.open_sftp.return_value = mock_sftp
    
    config = SSHConfig(host="localhost", username="user")
    client = SSHClient(config)
    client.download_file("remote.txt", "local.txt")
    
    mock_sftp.get.assert_called_once_with("remote.txt", "local.txt")
    mock_sftp.close.assert_called_once()

@patch("paramiko.SSHClient")
def test_ssh_close(mock_paramiko_cls):
    mock_client = mock_paramiko_cls.return_value
    config = SSHConfig(host="localhost", username="user")
    client = SSHClient(config)
    
    # Force client creation
    client._get_client()
    client.close()
    
    mock_client.close.assert_called_once()
    assert client._client is None

def test_ssh_close_not_opened():
    config = SSHConfig(host="localhost", username="user")
    client = SSHClient(config)
    client.close() # Should not raise error
