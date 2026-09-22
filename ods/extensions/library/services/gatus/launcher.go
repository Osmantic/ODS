package main

import (
 "bytes"
 "encoding/json"
 "fmt"
 "net/http"
 "net/url"
 "os"
 "os/exec"
 "os/signal"
 "syscall"
 "time"
)

func configuration(raw string) ([]byte, error) {
 u, err := url.Parse(raw)
 if err != nil || u.Hostname() == "" || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil || u.Fragment != "" {
  return nil, fmt.Errorf("GATUS_ENDPOINT must be an HTTP(S) URL without embedded credentials or a fragment")
 }
 c := map[string]any{
  "web": map[string]any{"port":8080},
  "storage": map[string]any{"type":"sqlite", "path":"/data/gatus.db"},
  "metrics":false,
  "endpoints": []any{map[string]any{"name":"Selected service", "url":raw, "interval":"1m", "conditions":[]string{"[STATUS] == 200"}, "client":map[string]any{"timeout":"10s", "ignore-redirect":true}}},
 }
 data, err := json.Marshal(c)
 // Gatus expands environment variables before parsing YAML/JSON. Preserve
 // literal dollar signs in URLs without allowing a second interpolation.
 return bytes.ReplaceAll(data, []byte("$"), []byte(`\u0024`)), err
}

func main() {
 if len(os.Args)>1 && os.Args[1]=="health" {
  c:=http.Client{Timeout:5*time.Second, CheckRedirect:func(*http.Request,[]*http.Request)error{return http.ErrUseLastResponse}}
  r,e:=c.Get("http://127.0.0.1:8080/health")
  if e!=nil {os.Exit(1)}; r.Body.Close(); if r.StatusCode!=200 {os.Exit(1)}; return
 }
 data, err:=configuration(os.Getenv("GATUS_ENDPOINT"))
 if err!=nil {fmt.Fprintln(os.Stderr,err);os.Exit(1)}
 if err=os.WriteFile("/tmp/ods-config.json", data,0600);err!=nil {fmt.Fprintln(os.Stderr,"Cannot write Gatus configuration");os.Exit(1)}
 cmd:=exec.Command("/gatus");cmd.Env=append(os.Environ(),"GATUS_CONFIG_PATH=/tmp/ods-config.json");cmd.Stdout=os.Stdout;cmd.Stderr=os.Stderr
 if err=cmd.Start();err!=nil {fmt.Fprintln(os.Stderr,"Cannot start Gatus");os.Exit(1)}
 signals:=make(chan os.Signal,2);signal.Notify(signals,os.Interrupt,syscall.SIGTERM)
 go func(){for s:=range signals {_=cmd.Process.Signal(s)}}()
 err=cmd.Wait();signal.Stop(signals);close(signals)
 if err!=nil {if e,ok:=err.(*exec.ExitError);ok {os.Exit(e.ExitCode())};os.Exit(1)}
}
