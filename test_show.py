import asyncio
import ollama

async def test_show():
    client = ollama.AsyncClient()
    info = await client.show("llama3.2:latest")
    print(dir(info))
    if hasattr(info, "template"):
        print("Template found!")
        print(info.template[:100])

if __name__ == "__main__":
    asyncio.run(test_show())
