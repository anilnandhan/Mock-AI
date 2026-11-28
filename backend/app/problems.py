import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .config import settings


class Example(BaseModel):
    input: str
    output: str
    explanation: str | None = None


class TrickyCase(BaseModel):
    input: str
    expected: str
    why: str


class Problem(BaseModel):
    id: str
    title: str
    topic: str
    difficulty: Literal["easy", "medium", "hard"]
    statement: str
    examples: list[Example] = Field(min_length=1)
    constraints: list[str]
    starter_code: str

    # Private to the interviewer: never sent to the browser.
    reference_solution: str
    optimal_complexity: str
    acceptable_approaches: list[str]
    tricky_cases: list[TrickyCase] = Field(min_length=1)
    clarifying_questions: list[str]
    hints: list[str] = Field(min_length=3, max_length=3)
    follow_ups: list[str] = Field(min_length=1)
    common_mistakes: list[str]


class PublicProblem(BaseModel):
    id: str
    title: str
    topic: str
    difficulty: str
    statement: str
    examples: list[Example]
    constraints: list[str]
    starter_code: str


def to_public(p: Problem) -> PublicProblem:
    return PublicProblem(**p.model_dump(include=set(PublicProblem.model_fields)))


def load_problems(directory: Path) -> dict[str, Problem]:
    problems: dict[str, Problem] = {}
    for path in sorted(directory.glob("*.json")):
        problem = Problem.model_validate(json.loads(path.read_text()))
        if problem.id != path.stem:
            raise ValueError(f"{path.name}: id '{problem.id}' must match the file name")
        problems[problem.id] = problem
    return problems


@lru_cache
def all_problems() -> dict[str, Problem]:
    return load_problems(settings.problems_dir)


def get_problem(problem_id: str) -> Problem | None:
    return all_problems().get(problem_id)
