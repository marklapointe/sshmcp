import asyncio
import json
import pytest
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocket
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from ssh_mcp_agent.ui.app import app, hosts_manager
from ssh_mcp_agent.hosts import Base, UserRecord, pwd_context

@pytest.fixture
def client():
    # Setup test DB
    test_db = "sqlite:///test_auth.db"
    hosts_manager.engine = create_engine(test_db)
    Base.metadata.create_all(hosts_manager.engine)
    hosts_manager.Session = sessionmaker(bind=hosts_manager.engine)
    
    # Add test users
    with hosts_manager.Session() as session:
        if not session.query(UserRecord).filter(UserRecord.username == "test_admin").first():
            session.add(UserRecord(
                username="test_admin",
                password_hash=pwd_context.hash("admin123"),
                role="admin"
            ))
        if not session.query(UserRecord).filter(UserRecord.username == "test_user").first():
            session.add(UserRecord(
                username="test_user",
                password_hash=pwd_context.hash("user123"),
                role="user"
            ))
        session.commit()
    
    with TestClient(app) as c:
        yield c

def test_login_and_roles(client):
    # Test admin login
    response = client.post("/token", data={"username": "test_admin", "password": "admin123"})
    assert response.status_code == 200
    admin_token = response.json()["access_token"]
    assert response.json()["role"] == "admin"
    
    # Test user login
    response = client.post("/token", data={"username": "test_user", "password": "user123"})
    assert response.status_code == 200
    user_token = response.json()["access_token"]
    assert response.json()["role"] == "user"
    
    # Test access control
    headers_user = {"Authorization": f"Bearer {user_token}"}
    headers_admin = {"Authorization": f"Bearer {admin_token}"}
    
    # Users should be able to get hosts but not add them
    response = client.get("/hosts", headers=headers_user)
    assert response.status_code == 200
    
    response = client.post("/hosts", headers=headers_user, json={
        "id": "test", "name": "test", "host": "test", "username": "test"
    })
    assert response.status_code == 403 # Forbidden
    
    # Admin should be able to add hosts
    response = client.post("/hosts", headers=headers_admin, json={
        "id": "test", "name": "test", "host": "test", "username": "test"
    })
    assert response.status_code == 200

def test_websocket_auth(client):
    # Try without token
    with client.websocket_connect("/ws") as websocket:
        msg = websocket.receive_json()
        assert msg["type"] == "error"
        assert "token missing" in msg["content"].lower()
        
    # Try with invalid token
    with client.websocket_connect("/ws?token=invalid") as websocket:
        msg = websocket.receive_json()
        assert msg["type"] == "error"
        assert "authentication failed" in msg["content"].lower()
        
    # Login to get token
    response = client.post("/token", data={"username": "test_admin", "password": "admin123"})
    token = response.json()["access_token"]
    
    # Connect with token
    with client.websocket_connect(f"/ws?token={token}") as websocket:
        # Should stay open
        pass

if __name__ == "__main__":
    # For manual running
    import sys
    pytest.main([__file__])
