package main

import (
	"os"
	"strings"
)

func main() {
	const name = "internal/cmd/root.go"
	b, e := os.ReadFile(name)
	if e != nil {
		panic(e)
	}
	s := strings.ReplaceAll(string(b), "\r\n", "\n")
	before := "\tif len(accounts) == 0 {\n\t\taccount := model.AccountDTO{"
	after := "\tif len(accounts) == 0 {\n\t\tinitialPassword, passwordErr := odsInitialPassword()\n\t\tif passwordErr != nil { logger.Fatal(passwordErr) }\n\t\taccount := model.AccountDTO{"
	if strings.Count(s, before) != 1 || strings.Count(s, `Password: "gopher"`) != 1 {
		panic("Shiori bootstrap contract changed")
	}
	s = strings.Replace(s, before, after, 1)
	s = strings.Replace(s, `Password: "gopher"`, `Password: initialPassword`, 1)
	if e = os.WriteFile(name, []byte(s), 0644); e != nil {
		panic(e)
	}
}
