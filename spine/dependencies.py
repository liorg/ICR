import os
from functools import lru_cache
from supabase import create_client, Client

@lru_cache()
def get_supabase() -> Client:
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
