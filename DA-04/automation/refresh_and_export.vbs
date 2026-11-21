' =====================================================================
' DA-04 Arcadia Lending - refresh the Excel board report and export it as PDF.
' Called by the Power Automate Desktop flow (step 2 of 3), or by hand:
'     cscript //nologo automation\refresh_and_export.vbs "C:\...\reports\Arcadia_Board_Report_2018-12.pdf"
'
' What it does, with Excel hidden:
'   1. opens excel\Arcadia_Board_Report.xlsx
'   2. Refresh All: reloads the CSVs in excel\data\ into the Power Pivot model and every PivotTable
'   3. exports the "Board Report" sheet's print area to the PDF path given (or reports\ by default)
'   4. saves and closes the workbook
' Exit code 0 = success, 1 = failure (Power Automate checks this). Prints one status line.
' Requires: excel\ folder added as a Trusted Location (otherwise Excel blocks the data refresh).
' =====================================================================
Option Explicit

Dim fso, baseDir, xlsxPath, pdfPath, xl, wb
Set fso = CreateObject("Scripting.FileSystemObject")
baseDir = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))   ' the DA-04 folder
xlsxPath = baseDir & "\excel\Arcadia_Board_Report.xlsx"
If WScript.Arguments.Count > 0 Then
    pdfPath = WScript.Arguments(0)
Else
    pdfPath = baseDir & "\reports\Arcadia_Board_Report.pdf"
End If

If Not fso.FileExists(xlsxPath) Then Fail "find workbook " & xlsxPath, ""
If Not fso.FolderExists(fso.GetParentFolderName(pdfPath)) Then fso.CreateFolder fso.GetParentFolderName(pdfPath)

On Error Resume Next

Set xl = CreateObject("Excel.Application")
If Err.Number <> 0 Then Fail "start Excel", Err.Description
xl.Visible = False
xl.DisplayAlerts = False          ' no pop-ups: nobody is there to click them

Set wb = xl.Workbooks.Open(xlsxPath)
If Err.Number <> 0 Then Fail "open workbook", Err.Description

wb.RefreshAll                     ' queries -> data model -> PivotTables
xl.CalculateUntilAsyncQueriesDone ' wait until every background refresh has finished
If Err.Number <> 0 Then Fail "refresh data", Err.Description

' 0 = PDF, 0 = standard quality, True = include document properties, False = respect the print area
wb.Worksheets("Board Report").ExportAsFixedFormat 0, pdfPath, 0, True, False
If Err.Number <> 0 Then Fail "export PDF", Err.Description

wb.Save
wb.Close False
xl.Quit
WScript.Echo "OK - board report exported to " & pdfPath
WScript.Quit 0


Sub Fail(stepName, detail)
    WScript.Echo "FAILED at step: " & stepName & IIf(detail <> "", " - " & detail, "")
    On Error Resume Next
    If IsObject(wb) Then wb.Close False
    If IsObject(xl) Then xl.Quit
    WScript.Quit 1
End Sub

Function IIf(condition, a, b)
    If condition Then IIf = a Else IIf = b
End Function
