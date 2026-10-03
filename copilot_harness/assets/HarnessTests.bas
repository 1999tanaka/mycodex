Attribute VB_Name = "HarnessTests"
Option Explicit

' Local Coding Harness 用のテストモジュール (ひな形)
'
' RunAll は「TEST:<名前>:<PASS|FAIL>[:詳細]」の行を改行区切りで返す Function です。
'   自動モード: Harness が Excel から RunAll を呼び出して結果を判定します (builtin:vba-test)。
'   手動モード: VBE のイミディエイト ウィンドウで PrintAll を実行し、表示された TEST 行をコピーして
'               python harness.py result --paste を実行します。
'
' テストの追加例:
'   r = r & Check("TAX_CALC", CalcTax(1000) = 1100, "CalcTax(1000) が 1100 ではない")

Public Function RunAll() As String
    Dim r As String
    r = r & Check("SAMPLE_SUM", Application.WorksheetFunction.Sum(1, 2) = 3, "1 + 2 が 3 ではない")
    ' r = r & Check("MY_TEST", MyFunction() = 期待値, "失敗時の説明")
    RunAll = r & "TEST:END" & vbLf
End Function

Public Sub PrintAll()
    Debug.Print RunAll()
End Sub

Private Function Check(ByVal testName As String, ByVal ok As Boolean, Optional ByVal detail As String = "") As String
    If ok Then
        Check = "TEST:" & testName & ":PASS" & vbLf
    Else
        Check = "TEST:" & testName & ":FAIL:" & detail & vbLf
    End If
End Function
