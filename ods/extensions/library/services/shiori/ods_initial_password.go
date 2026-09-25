package cmd

import (
	"fmt"
	"os"
)

func odsInitialPassword() (string, error) {
	password := os.Getenv("SHIORI_INITIAL_PASSWORD")
	// bcrypt's limit is in bytes, including for non-ASCII passwords.
	if len(password) < 12 || len(password) > 72 {
		return "", fmt.Errorf("SHIORI_INITIAL_PASSWORD must contain 12 to 72 bytes for first account creation")
	}
	return password, nil
}
