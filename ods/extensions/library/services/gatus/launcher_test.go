package main
import("encoding/json";"strings";"testing")
func TestConfigurationPreservesURLWithoutEnvironmentExpansion(t *testing.T){
 input:=`https://example.test/check?q=$HOME&quoted="value"`
 data,err:=configuration(input);if err!=nil{t.Fatal(err)}
 if strings.Contains(string(data),"$"){t.Fatal("URL can be interpolated")}
 var parsed struct{Endpoints []struct{URL string `json:"url"`} `json:"endpoints"`}
 if err=json.Unmarshal(data,&parsed);err!=nil{t.Fatal(err)}
 if parsed.Endpoints[0].URL!=input{t.Fatal("URL changed")}
}
func TestConfigurationRejectsUnsupportedTargetsWithoutEcho(t *testing.T){
 for _,input:=range []string{"", "file:///secret", "http://", "https://user:secret@example.test", "https://example.test/#secret"}{
  _,err:=configuration(input);if err==nil{t.Fatalf("accepted %q",input)}
  if strings.Contains(err.Error(),"secret"){t.Fatal("input echoed")}
 }
}
