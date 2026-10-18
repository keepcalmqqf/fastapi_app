"""邮件发送（可插拔）。

脚手架不内置 SMTP 依赖：默认实现只把验证链接写进日志，本地开发与联调足够。
接入真实邮件服务（SMTP / 事务邮件 API）时替换 `send_verification_email` 的实现即可，
调用方无需改动。
"""

import logging

logger = logging.getLogger("fastapi_app")


async def send_verification_email(email: str, link: str) -> None:
    """发送邮箱验证邮件。

    默认只记日志。接真实服务时在这里调用 SMTP 或邮件 API；发送失败应抛出，
    由调用方决定是否影响主流程（当前注册流程对发送失败是容忍的）。
    """
    logger.info("邮箱验证链接（未配置邮件服务，仅记录日志）: to=%s link=%s", email, link)


async def send_account_exists_email(email: str) -> None:
    """「该邮箱已注册」提示邮件。

    注册接口对已注册邮箱不再回 409（避免邮箱枚举），改为统一文案 + 投递本邮件：
    真实用户据此直接登录或重置密码，攻击者则得不到任何区分信号。
    接入真实邮件服务后这里应加节流（同一邮箱的发送频次），避免被用作邮件轰炸。
    """
    logger.info("账号已存在提示邮件（未配置邮件服务，仅记录日志）: to=%s", email)
