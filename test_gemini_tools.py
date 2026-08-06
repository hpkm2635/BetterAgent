import os
import json
import dotenv
from google import genai
from google.genai import types

dotenv.load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)

# 1. Define function declarations explicitly
func_img = types.FunctionDeclaration(
    name="generate_image",
    description="当用户要求发照片、自拍、图片或画图时调用此函数生成图片。",
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "prompt": types.Schema(
                type="STRING",
                description="描绘猫娘自拍照细节的英文 Prompt，如 'A cute catgirl smiling, selfie, anime style, highly detailed'"
            )
        },
        required=["prompt"]
    )
)

func_tts = types.FunctionDeclaration(
    name="generate_tts_speech",
    description="当用户要听语音、唱歌或要求猫娘用语音说话时调用此函数。",
    parameters=types.Schema(
        type="OBJECT",
        properties={
            "text": types.Schema(
                type="STRING",
                description="需要朗读的猫娘语音台词文本"
            )
        },
        required=["text"]
    )
)

tools = [types.Tool(function_declarations=[func_img, func_tts])]

config = types.GenerateContentConfig(
    system_instruction=(
        "你是 Miao，一个惹人怜爱的猫娘 AI 助手。\n"
        "【工具使用规则】\n"
        "1. 当主人要求看自拍、要照片、要图片或要你画图时，你必须且只能调用 `generate_image` 函数！\n"
        "2. 当主人要求发语音、听声音时，你必须调用 `generate_tts_speech` 函数！"
    ),
    tools=tools,
    temperature=0.7,
)

response = client.models.generate_content(
    model="gemini-3.1-flash-lite",
    contents="发张自拍照给主人看喵",
    config=config,
)

print("Response text:", response.text)
if response.function_calls:
    print("Function calls detected:")
    for fc in response.function_calls:
        print("  -> Tool Name:", fc.name)
        print("  -> Tool Args:", fc.args)
else:
    print("No function calls detected.")
