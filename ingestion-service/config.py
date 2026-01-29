from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    MESSAGE: str = "TTRSS Ingestion Service"
    TTRSS_URL: str
    TTRSS_USER: str
    TTRSS_PASSWORD: str
    
    # Optional: Defaults for local dev if needed, or left strictly as required env vars
    
    class Config:
        env_file = ".env"

settings = Settings()
