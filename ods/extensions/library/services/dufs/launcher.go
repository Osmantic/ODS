package main
import("fmt";"net/http";"os";"os/exec";"os/signal";"strings";"syscall";"time")

func authRule(password string)(string,error){
 if len(password)<12 || strings.ContainsAny(password,":@|\r\n\x00") || strings.HasPrefix(password,"$6$") {
  return "",fmt.Errorf("DUFS_PASSWORD needs at least 12 bytes and cannot contain colon, at-sign, pipe, line breaks or a crypt-hash prefix")
 }
 return "ods:"+password+"@/:rw",nil
}
func main(){
 if len(os.Args)>1 && os.Args[1]=="health"{
  c:=http.Client{Timeout:5*time.Second,CheckRedirect:func(*http.Request,[]*http.Request)error{return http.ErrUseLastResponse}}
  r,e:=c.Get("http://127.0.0.1:5000/__dufs__/health");if e!=nil{os.Exit(1)};r.Body.Close();if r.StatusCode!=200{os.Exit(1)};return
 }
 rule,err:=authRule(os.Getenv("DUFS_PASSWORD"));if err!=nil{fmt.Fprintln(os.Stderr,err);os.Exit(1)}
 cmd:=exec.Command("/bin/dufs","/data","--bind","0.0.0.0","--port","5000","--allow-upload","--allow-delete","--allow-search","--allow-archive")
 env:=[]string{};for _,value:=range os.Environ(){if !strings.HasPrefix(value,"DUFS_AUTH=")&&!strings.HasPrefix(value,"DUFS_PASSWORD="){env=append(env,value)}};cmd.Env=append(env,"DUFS_AUTH="+rule)
 cmd.Stdout=os.Stdout;cmd.Stderr=os.Stderr
 if err=cmd.Start();err!=nil{fmt.Fprintln(os.Stderr,"Cannot start Dufs");os.Exit(1)}
 signals:=make(chan os.Signal,2);signal.Notify(signals,os.Interrupt,syscall.SIGTERM);go func(){for s:=range signals{_ = cmd.Process.Signal(s)}}()
 err=cmd.Wait();signal.Stop(signals);close(signals);if err!=nil{if e,ok:=err.(*exec.ExitError);ok{os.Exit(e.ExitCode())};os.Exit(1)}
}
