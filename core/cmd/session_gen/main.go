package main

import (
	"bufio"
	"context"
	"fmt"
	"os"
	"strconv"
	"strings"

	"github.com/gotd/td/session"
	"github.com/gotd/td/telegram"
	"github.com/gotd/td/telegram/auth"
	"github.com/gotd/td/tg"
	"go.uber.org/zap"
)

func main() {
	reader := bufio.NewReader(os.Stdin)

	fmt.Println("=== Gotd Session 文件生成器 ===")

	fmt.Print("请输入 API ID (例如 123456): ")
	apiIDStr, _ := reader.ReadString('\n')
	apiID, _ := strconv.Atoi(strings.TrimSpace(apiIDStr))

	fmt.Print("请输入 API Hash: ")
	apiHashStr, _ := reader.ReadString('\n')
	apiHash := strings.TrimSpace(apiHashStr)

	fmt.Print("请输入手机号 (包含国家代码, 例如 +1 1234567890): ")
	phoneStr, _ := reader.ReadString('\n')
	phone := strings.TrimSpace(phoneStr)

	logger, _ := zap.NewDevelopment()
	sessionStorage := &session.FileStorage{Path: "gotd.session.json"}

	client := telegram.NewClient(apiID, apiHash, telegram.Options{
		Logger:         logger,
		SessionStorage: sessionStorage,
	})

	ctx := context.Background()

	err := client.Run(ctx, func(ctx context.Context) error {
		flow := auth.NewFlow(
			auth.Constant(phone, "", auth.CodeAuthenticatorFunc(func(ctx context.Context, sentCode *tg.AuthSentCode) (string, error) {
				fmt.Print("请输入 Telegram 收到的 5 位验证码: ")
				code, _ := reader.ReadString('\n')
				return strings.TrimSpace(code), nil
			})),
			auth.SendCodeOptions{},
		)

		if err := client.Auth().IfNecessary(ctx, flow); err != nil {
			return err
		}

		me, err := client.Self(ctx)
		if err != nil {
			return err
		}

		fmt.Printf("\n授权成功！登录账号: %s (@%s), ID: %d\n", me.FirstName, me.Username, me.ID)
		fmt.Println("Session 凭证已成功保存至 core/gotd.session.json！")
		return nil
	})

	if err != nil {
		fmt.Printf("生成 Session 失败: %v\n", err)
	}
}
