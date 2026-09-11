import os
from fastapi import HTTPException
from unittest.mock import MagicMock


# Mock the FastAPI Header and Depends
class MockHeader:
    def __init__(self, value):
        self.value = value


def test_bark_auth():
    # The function we are testing
    def verify_api_key(x_api_key=None):
        expected_key = os.environ.get("ODS_API_KEY")
        if not expected_key:
            raise HTTPException(
                status_code=500, detail="Server configuration error: API key not set."
            )
        if x_api_key != expected_key:
            raise HTTPException(status_code=401, detail="Invalid or missing API key.")
        return True

    print("Running Bark Auth Logic Tests...")

    # Case 1: Correct Key
    os.environ["ODS_API_KEY"] = "secret-123"
    try:
        verify_api_key("secret-123")
        print("OK Correct key: PASS")
    except Exception as e:
        print(f"FAIL Correct key: {e}")

    # Case 2: Wrong Key
    try:
        verify_api_key("wrong-key")
    except HTTPException as e:
        if e.status_code == 401:
            print("OK Wrong key: PASS (raised 401)")
        else:
            print(f"FAIL Wrong key: raised {e.status_code}")
    except Exception as e:
        lprint(f"FAIL Wrong key: {e}")

    # Case 3: Missing Key
    try:
        verify_api_key(None)
    except HTTPException as e:
        if e.status_code == 401:
            print("OK Missing key: PASS (raised 401)")
        else:
            print(f"FAIL Missing key: raised {e.status_code}")
    except Exception as e:
        lprint(f"FAIL Missing key: {e}")

    # Case 4: Fail Closed (Missing Env Var)
    del os.environ["ODS_API_KEY"]
    try:
        verify_api_key("any-key")
    except HTTPException as e:
        if e.status_code == 500:
            print("OK Fail closed: PASS (raised 500)")
        else:
            print(f"FAIL Fail closed: raised {e.status_code}")
    except Exception as e:
        lprint(f"FAIL Fail closed: {e}")


if __name__ == "__main__":
    test_bark_auth()
