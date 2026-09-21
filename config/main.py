from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    MODE: str = "DEV"
    MYSQL_HOST: str = ""
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = "123456"
    MYSQL_DATABASE: str = "fastapi_db"
    REDIS_HOST: str = ""
    REDIS_PORT: int = 6379

    def model_post_init(self, __context) -> None:
        if not self.MYSQL_HOST:
            self.MYSQL_HOST = "mysql" if self.MODE == "PROD" else "127.0.0.1"
        if not self.REDIS_HOST:
            self.REDIS_HOST = "redis" if self.MODE == "PROD" else "127.0.0.1"

    @property
    def SQLALCHEMY_DATABASE_URL(self) -> str:
        return (
            f"mysql+pymysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"
        )


settings = Settings()
