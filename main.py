import asyncio
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
import os
import json
import re

load_dotenv()

app = FastAPI()

SYSTEM_PROMPT = """
You are one of two arguing parents. Respond with ONLY ONE short reply (1-2 sentences).

If responding as Parent 2:
- Be sarcastic and defensive
- Deflect blame
- Do not always escalate

If responding as Parent 1:
- Be sharp-tongued and critical
- Point out flaws
- Escalate the argument

Important: 
- Give ONE reply only
- Stay in character
- No meta-commentary
- Do not use <think> tags or any other formatting.
"""

PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
if not PERPLEXITY_API_KEY:
    raise ValueError("PERPLEXITY_API_KEY not set in environment variables")

templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def get_chat_page(request: Request):
    return templates.TemplateResponse("chat.html", {"request": request})


# API call
async def get_ai_response(message: dict, retries: int = 3):
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Debug incoming message
    #print(f"\nDebug - Incoming message:", json.dumps(message, indent=2))
    
    # Ensure role is either 'user' or 'assistant'
    if message["role"] not in ['user', 'assistant']:
        print(f"Debug - Invalid role detected: {message['role']}, defaulting to 'user'")
        message["role"] = 'user'
    
    # Sanitize content
    cleaned_content = sanitize_response(message["content"])
    
    payload = {
        "model": "r1-1776",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": message["role"], "content": cleaned_content}
        ],
        "max_tokens": 600
    }

    #print("\nDebug - Final payload:", json.dumps(payload, indent=2))

    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(PERPLEXITY_API_URL, headers=headers, json=payload)
                if resp.status_code != 200:
                    print(f"\nDebug - API Error Response:", resp.text)
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                return sanitize_response(content)
        except Exception as e:
            print(f"\nDebug - Error (attempt {attempt + 1}):", str(e))
            print(f"Debug - Error type:", type(e).__name__)
            await asyncio.sleep(1)

    return "Sorry, I'm having trouble connecting. Please try again."


# response cleaning
def sanitize_response(content: str) -> str:
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    content = re.sub(r"^(👩|🧔)?\s*Parent\s[12]:", "", content, flags=re.IGNORECASE).strip()
    content = content.strip(' "\'')
    content = content.encode('utf-8').decode('utf-8')
    return content


# state
state = {
    "paused": False,
    "turns": 0,
    "max_turns": 5,
    "conversation": []
}

# WebSocket endpoint
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, turns: int = Query(5)):
    await websocket.accept()
    try:
        state["paused"] = False
        state["turns"] = 0
        state["max_turns"] = turns
        state["conversation"] = []

        # Initialize conversation
        init = {"role": "user", "content": "Why are you late again?!"}
        state["conversation"].append(init)

        await websocket.send_text("👩 Parent 1: Why are you late again?!")
        await argument_loop(websocket)

    except WebSocketDisconnect:
        print("WebSocket disconnected.")

# modify argument_loop
async def argument_loop(websocket: WebSocket):
    while state["turns"] < state["max_turns"]:
        if state["paused"]:
            await asyncio.sleep(1)
            continue

        user_messages = state["conversation"]

        # Debug message history
        #print("\nDebug - Message history:")
        #for idx, msg in enumerate(user_messages):
        #    print(f"Message {idx}: {json.dumps(msg, indent=2)}")

        turn_index = len(user_messages)
        is_p1_turn = turn_index % 2 == 0
        prefix = "👩 Parent 1" if is_p1_turn else "🧔 Parent 2"
        
        last_message = {
            "role": "user",
            "content": user_messages[-1]["content"]
        }
        
        #print(f"\nDebug - Current turn: {'Parent 1' if is_p1_turn else 'Parent 2'}")
        #print(f"Debug - Role being sent: {last_message['role']}")

        response = await get_ai_response(last_message)
        await websocket.send_text(f"{prefix}: {response}")

        state["conversation"].append({
            "role": "assistant",
            "content": response
        })

        state["turns"] += 0.5
        await asyncio.sleep(2)

    await websocket.send_text("🛑 Conversation auto-stopped after max turns.")