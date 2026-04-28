try:
    from google.oauth2 import id_token
    print("SUCCESS: google.oauth2.id_token imported successfully!")
except ImportError as e:
    print(f"FAILURE: {e}")
