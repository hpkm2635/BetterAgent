import asyncio
from telethon import TelegramClient, errors

async def main():
    print("=== Telegram Session 生成器 ===")
    
    # 1. 获取 API ID 和 API Hash
    # 你可以从 https://my.telegram.org 获取这些信息
    try:
        api_id = int(input("请输入 API ID: ").strip())
        api_hash = input("请输入 API Hash: ").strip()
    except ValueError:
        print("错误: API ID 必须是数字。")
        return

    # 定义 session 文件的名称
    session_name = input("请输入生成的 Session 文件名 (例如 'my_account'): ").strip()
    if not session_name:
        session_name = 'my_account'

    # 2. 初始化客户端
    client = TelegramClient(session_name, api_id, api_hash)

    # 3. 连接到 Telegram 服务器
    print("正在连接到 Telegram 服务器...")
    await client.connect()

    # 4. 检查是否已经授权
    if not await client.is_user_authorized():
        phone_number = input("请输入手机号 (包含国家代码, 例如 +8613800000000): ").strip()

        try:
            # 发送验证码请求
            print(f"正在向 {phone_number} 发送验证码...")
            sent_code = await client.send_code_request(phone_number)
        except errors.FloodWaitError as e:
            print(f"错误: 请求过多，请在 {e.seconds} 秒后重试。")
            return
        except Exception as e:
            print(f"发送验证码时出错: {e}")
            return

        # 输入验证码
        code = input("请输入你收到的验证码: ").strip()

        try:
            # 登录
            await client.sign_in(phone_number, code, phone_code_hash=sent_code.phone_code_hash)
        except errors.SessionPasswordNeededError:
            # 如果开启了两步验证 (2FA)，需要输入密码
            password = input("该账号开启了两步验证，请输入密码: ")
            try:
                await client.sign_in(password=password)
            except Exception as e:
                print(f"登录失败 (密码错误?): {e}")
                return
        except Exception as e:
            print(f"登录失败: {e}")
            return

    print(f"\n成功! Session 文件已生成: {session_name}.session")
    
    # 获取并打印当前用户信息以确认
    me = await client.get_me()
    print(f"已登录为: {me.first_name} (ID: {me.id})")

    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())