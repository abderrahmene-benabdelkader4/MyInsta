from fastapi import FastAPI, HTTPException, File, UploadFile, Form, Depends
from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTable
from imagekitio import ImageKit
from sqlalchemy.testing.plugin.plugin_base import options
from sqlalchemy.orm import selectinload
from app.schemas import PostCreate, PostResponse, UserCreate, UserRead, UserUpdate
from app.db import Post, create_db_and_tables, get_async_session, User
from sqlalchemy.ext.asyncio import AsyncSession
from contextlib import asynccontextmanager
from sqlalchemy import select
from app.images import imagekit
import shutil
import os
import tempfile
import uuid
from app.users import fastapi_users, auth_backend, current_active_user

@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_db_and_tables()
    yield

app = FastAPI(lifespan=lifespan)

app.include_router(fastapi_users.get_auth_router(auth_backend), prefix='/auth/jwt', tags=['auth'])
app.include_router(fastapi_users.get_register_router(UserRead, UserCreate), prefix='/auth', tags=['auth'])
app.include_router(fastapi_users.get_reset_password_router(), prefix='/auth', tags=['auth'])
app.include_router(fastapi_users.get_verify_router(UserRead), prefix='/auth', tags=['auth'])
app.include_router(fastapi_users.get_users_router(UserRead, UserUpdate), prefix='/users', tags=['users'])

from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession
import os

@app.post('/upload')
async def upload(
    file: UploadFile = File(...),
    caption: str = Form(""),
        user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session)
):
    try:
        # 1. Fallback for missing or empty filenames
        filename = file.filename or "uploaded_file"

        # 2. Read file bytes asynchronously
        file_bytes = await file.read()

        # 3. Offload synchronous ImageKit call to a threadpool to avoid blocking the event loop
        upload_result = await run_in_threadpool(
            imagekit.files.upload,
            file=file_bytes,
            file_name=filename,
            tags=['backend-upload']
        )

        # 4. Save metadata to the database
        post = Post(
            user_id = user.id,
            caption=caption,
            url=upload_result.url,
            file_type=upload_result.file_type,
            file_name=upload_result.name
        )
        session.add(post)
        await session.commit()
        await session.refresh(post)

        return post

    except Exception as e:
        await session.rollback()  # Roll back DB state in case of commit errors
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        await file.close()  # Properly close FastAPI's UploadFile stream


@app.get('/feed')
@app.get('/feed')
async def get_feed(session: AsyncSession = Depends(get_async_session)):
    # 1. Fetch posts and eagerly load the associated user relationship
    result = await session.execute(
        select(Post).options(selectinload(Post.user)).order_by(Post.created_at.desc())
    )
    posts = result.scalars().all()

    posts_data = []
    for post in posts:
        posts_data.append(
            {
                "id": str(post.id),
                "caption": post.caption,
                "url": post.url,
                "file_type": post.file_type,   # Changed space to underscore
                "file_name": post.file_name,   # Changed space to underscore
                "created_at": post.created_at.isoformat(),  # Changed space to underscore
                "email": post.user.email if post.user else "Anonymous"  # 👈 Added email
            }
        )

    return {"posts": posts_data}

@app.delete('/post/{post_id}')
async def delete(post_id: str, session: AsyncSession = Depends(get_async_session),
                 user : User = Depends(current_active_user)):
    try:
        post_uuid = uuid.UUID(post_id)
        result = await session.execute(select(Post).where(Post.id==post_uuid))
        post = result.scalars().first()
        if not post:
            raise HTTPException(status_code=404, detail='Post Not Found')
        if post.user_id != user.id:
            raise HTTPException(status_code=403, detail="You don't have permission to delete this post.")
        await session.delete(post)
        await session.commit()
        return {"success":True, "message": "Post deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



