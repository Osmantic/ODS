package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
)

func TestBrowserAndAPIAuthentication(t *testing.T) {
	calls := 0
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Header.Get("Authorization") != "Bearer test-local-key" {
			t.Error("wrong upstream credential")
		}
		if r.URL.Path != "/api/collections" || r.URL.RawQuery != "page=2" {
			t.Error("request path lost")
		}
		body, _ := io.ReadAll(r.Body)
		if string(body) != "payload" {
			t.Error("body lost")
		}
		w.WriteHeader(201)
		w.Write([]byte("saved"))
	}))
	defer backend.Close()
	target, _ := url.Parse(backend.URL)
	app := handler("test-local-key", target)
	for _, mode := range []string{"browser", "api", "invalid", "none"} {
		request := httptest.NewRequest("POST", "/api/collections?page=2", strings.NewReader("payload"))
		switch mode {
		case "browser":
			request.SetBasicAuth("ods", "test-local-key")
		case "api":
			request.Header.Set("Authorization", "Bearer test-local-key")
		case "invalid":
			request.SetBasicAuth("ods", "wrong")
		}
		response := httptest.NewRecorder()
		app.ServeHTTP(response, request)
		if mode == "browser" || mode == "api" {
			if response.Code != 201 || response.Body.String() != "saved" {
				t.Fatal(response.Code, response.Body.String())
			}
		} else if response.Code != 401 {
			t.Fatal("unauthorized request accepted")
		}
	}
	if calls != 2 {
		t.Fatal("unauthorized requests reached backend", calls)
	}
}

func TestHealthHidesDataAndRejectsRedirects(t *testing.T) {
	status := 200
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer test-local-key" {
			t.Error("health probe missing credential")
		}
		w.Header().Set("Location", "http://127.0.0.1:1/never-follow")
		w.WriteHeader(status)
		w.Write([]byte("private collection names"))
	}))
	defer backend.Close()
	target, _ := url.Parse(backend.URL)
	app := handler("test-local-key", target)
	for _, code := range []int{200, 401, 302, 500} {
		status = code
		response := httptest.NewRecorder()
		app.ServeHTTP(response, httptest.NewRequest("GET", "/health", nil))
		expected := 503
		if code == 200 {
			expected = 204
		}
		if response.Code != expected || response.Body.Len() != 0 {
			t.Fatal("health response leaked data or accepted failure", response.Code, response.Body.String())
		}
	}
}

func TestBrowserCrossOriginWriteCannotReachBackend(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { t.Error("cross-origin write forwarded") }))
	defer backend.Close()
	target, _ := url.Parse(backend.URL)
	request := httptest.NewRequest("POST", "http://localhost:11024/api/collections", strings.NewReader("{}"))
	request.SetBasicAuth("ods", "test-local-key")
	request.Header.Set("Origin", "https://unrelated.example")
	response := httptest.NewRecorder()
	handler("test-local-key", target).ServeHTTP(response, request)
	if response.Code != 403 {
		t.Fatal("cross-origin write accepted", response.Code)
	}
}
