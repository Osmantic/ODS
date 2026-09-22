package main
import("strings";"testing")
func TestCannotInjectAnonymousRule(t *testing.T){
 for _,input:=range []string{"short","long-password@/","long-password|@/","long:password123","long-password\n","$6$fakehashlong"}{
  _,err:=authRule(input);if err==nil{t.Fatal("accepted auth syntax")};if strings.Contains(err.Error(),input){t.Fatal("credential echoed")}
 }
}
func TestLiteralStrongPassword(t *testing.T){
 input:="correct horse $battery#staple";r,e:=authRule(input);if e!=nil||r!="ods:"+input+"@/:rw"{t.Fatal("literal password not preserved")}
}
