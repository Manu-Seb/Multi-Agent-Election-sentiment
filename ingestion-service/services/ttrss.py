import httpx
from typing import List, Dict, Any, Optional
from config import settings
import logging

logger = logging.getLogger(__name__)

class TTRSSClient:
    def __init__(self):
        self.base_url = settings.TTRSS_URL
        self._sid: Optional[str] = None
        # Log the config (masking password)
        logger.info(f"TTRSSClient initialized with URL: {self.base_url}, User: {settings.TTRSS_USER}")

    async def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        # Disable SSL verification for development/internal use if needed
        logger.debug(f"Sending POST to {self.base_url} with op={payload.get('op')}")
        async with httpx.AsyncClient(verify=False) as client:
            try:
                response = await client.post(self.base_url, json=payload, timeout=10.0)
                logger.info(f"Response Status: {response.status_code}")
                response.raise_for_status()
                data = response.json()
                
                if data.get("status") != 0:
                    error_content = data.get("content", {})
                    error_msg = error_content.get("error", "Unknown TT-RSS Error") if isinstance(error_content, dict) else str(error_content)
                    logger.error(f"TT-RSS Logic Error: {error_msg}")
                    raise Exception(f"TT-RSS API Error: {error_msg}")
                    
                return data
            except httpx.HTTPError as e:
                logger.error(f"HTTP Error communicating with TTRSS at {self.base_url}: {e}")
                raise

    async def login(self) -> str:
        logger.info("Attempting login...")
        payload = {
            "op": "login",
            "user": settings.TTRSS_USER,
            "password": settings.TTRSS_PASSWORD
        }
        data = await self._post(payload)
        self._sid = data["content"]["session_id"]
        logger.info(f"Login successful. Session ID obtained.")
        return self._sid

    async def get_session(self) -> str:
        if self._sid is None:
            await self.login()
        return self._sid

    async def get_headlines(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        sid = await self.get_session()
        payload = {
            "op": "getHeadlines",
            "sid": sid,
            "feed_id": -4, 
            "is_cat": False,
            "q": query,
            "show_content": True,
            "view_mode": "all_articles",
            "limit": limit
        }
        
        try:
            data = await self._post(payload)
            return data["content"]
        except Exception as e:
            if "NOT_LOGGED_IN" in str(e) or "Access denied" in str(e):
                logger.warning(f"Session issue encountered: {e}. Retrying login...")
                sid = await self.login()
                payload["sid"] = sid
                data = await self._post(payload)
                return data["content"]
            raise e
