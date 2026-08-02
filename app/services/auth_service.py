import asyncio

from passlib.context import CryptContext

from core.config import settings

# Configure bcrypt with configurable rounds
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=settings.bcrypt_rounds,
)


async def hash_password(password: str) -> str:
    loop = asyncio.get_running_loop()
    return str(await loop.run_in_executor(None, pwd_context.hash, password))


async def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plaintext password against a hashed password.
    
    Parameters:
        plain_password (str): The plaintext password to verify.
        hashed_password (str): The hashed password to compare against.
    
    Returns:
        bool: `True` if the plaintext password matches the hash, `False` otherwise.
    """
    loop = asyncio.get_running_loop()
    return bool(
        await loop.run_in_executor(None, pwd_context.verify, plain_password, hashed_password),
    )
