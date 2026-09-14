from fastapi import FastAPI
from pydantic import BaseModel

intro = FastAPI()

@intro.get("/")
def hello():
    return "hello world"

@intro.get("/param")
def try_param(name: str, age: int):
    return f"Hello {name}, you are {age} years old"

class UserInput(BaseModel):
    name: str
    age: int

@intro.post("/register_user")
def register_user(user_input: UserInput):
    return f"Hello {user_input.name}, you are {user_input.age} years old"