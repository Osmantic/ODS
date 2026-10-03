package cmd

import (
	"strings"
	"testing"
)

func TestInitialPasswordBounds(t *testing.T) {
	for _, value := range []string{"", "gopher", strings.Repeat("a", 73), strings.Repeat("界", 25)} {
		t.Setenv("SHIORI_INITIAL_PASSWORD", value)
		got, err := odsInitialPassword()
		if err == nil || got != "" {
			t.Fatal("invalid initial secret accepted")
		}
		if value != "" && strings.Contains(err.Error(), value) {
			t.Fatal("error exposed secret")
		}
	}
	for _, value := range []string{strings.Repeat("a", 12), strings.Repeat("a", 72), strings.Repeat("界", 24)} {
		t.Setenv("SHIORI_INITIAL_PASSWORD", value)
		got, err := odsInitialPassword()
		if err != nil || got != value {
			t.Fatal("valid secret altered or rejected")
		}
	}
}
