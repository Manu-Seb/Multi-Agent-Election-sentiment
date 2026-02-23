from pydantic_settings import BaseSettings
from pydantic import ConfigDict

class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", extra="ignore")

    MESSAGE: str = "TTRSS Ingestion Service"
    TTRSS_URL: str = "http://localhost/tt-rss/api/"
    TTRSS_USER: str = "admin"
    TTRSS_PASSWORD: str = "password"

settings = Settings()
