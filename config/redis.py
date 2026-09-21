from redis.asyncio import Redis

from config.main import settings


def create_redis() -> Redis:
    return Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, decode_responses=True)
