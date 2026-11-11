# 改写自 DeepSeek Harness 桌面版 office-skills（MIT 许可）。这个脚本是新写的：用本机 Microsoft Office 的 COM 自动化，
# 全程不可见、不弹窗，做完关掉自己开的程序。人已经开着的 Word / PowerPoint / Excel 不会被关。
# 用法（一般由 officekit.py 调用）：
#   powershell -NoProfile -File office_com.ps1 -Action pdf    -In a.docx -Out a.pdf
#   powershell -NoProfile -File office_com.ps1 -Action slides -In a.pptx -Out 文件夹 -Width 1600 [-Pages "1,3"]
#   powershell -NoProfile -File office_com.ps1 -Action recalc -In a.xlsx -Out a-重算.xlsx
param(
    [Parameter(Mandatory = $true)][ValidateSet('pdf', 'slides', 'recalc')][string]$Action,
    [Parameter(Mandatory = $true)][string]$In,
    [Parameter(Mandatory = $true)][string]$Out,
    [int]$Width = 1600,
    [string]$Pages = ''
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$In = [IO.Path]::GetFullPath($In)
$Out = [IO.Path]::GetFullPath($Out)
$ext = [IO.Path]::GetExtension($In).ToLower()

function Release($o) { if ($o) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($o) } }

if ($ext -in '.doc', '.docx', '.docm', '.rtf', '.odt') { $kind = 'word' }
elseif ($ext -in '.ppt', '.pptx', '.pptm', '.odp') { $kind = 'ppt' }
elseif ($ext -in '.xls', '.xlsx', '.xlsm', '.xlsb', '.ods', '.csv') { $kind = 'excel' }
else { throw "不认识的文件类型：$ext" }

$app = $null; $doc = $null
try {
    if ($kind -eq 'word') {
        $had = @(Get-Process WINWORD -ErrorAction SilentlyContinue).Count
        $app = New-Object -ComObject Word.Application
        $app.Visible = $false
        $app.DisplayAlerts = 0
        # Open(FileName, ConfirmConversions, ReadOnly, AddToRecentFiles)
        $doc = $app.Documents.Open($In, $false, $true, $false)
        $doc.Repaginate()
        $n = $doc.ComputeStatistics(2)   # wdStatisticPages
        $doc.ExportAsFixedFormat($Out, 17)   # wdExportFormatPDF
        $doc.Close(0)
        Write-Output "{""ok"":true,""engine"":""Word"",""pages"":$n}"
    }
    elseif ($kind -eq 'ppt') {
        $had = @(Get-Process POWERPNT -ErrorAction SilentlyContinue).Count
        $app = New-Object -ComObject PowerPoint.Application
        # Open(FileName, ReadOnly, Untitled, WithWindow=false)：不出窗口
        $doc = $app.Presentations.Open($In, -1, 0, 0)
        $n = $doc.Slides.Count
        if ($Action -eq 'slides') {
            [void](New-Item -ItemType Directory -Force $Out)
            $h = [int]($Width * $doc.PageSetup.SlideHeight / $doc.PageSetup.SlideWidth)
            $want = if ($Pages) { $Pages -split ',' | % { [int]$_ } } else { 1..$n }
            foreach ($i in $want) { $doc.Slides.Item($i).Export((Join-Path $Out ('slide-{0:D2}.png' -f $i)), 'PNG', $Width, $h) }
        }
        else { $doc.SaveAs($Out, 32) }   # ppSaveAsPDF
        $doc.Close()
        Write-Output "{""ok"":true,""engine"":""PowerPoint"",""pages"":$n}"
    }
    else {
        $had = @(Get-Process EXCEL -ErrorAction SilentlyContinue).Count
        $app = New-Object -ComObject Excel.Application
        $app.Visible = $false
        $app.DisplayAlerts = $false
        $app.AskToUpdateLinks = $false
        $doc = $app.Workbooks.Open($In, 0, ($Action -ne 'recalc'))
        if ($Action -eq 'recalc') {
            $app.CalculateFull()
            $fmt = @{ '.xlsx' = 51; '.xlsm' = 52; '.xls' = 56; '.xlsb' = 50 }[[IO.Path]::GetExtension($Out).ToLower()]
            if (-not $fmt) { $fmt = 51 }
            $doc.SaveAs($Out, $fmt)
        }
        else { $doc.ExportAsFixedFormat(0, $Out) }   # xlTypePDF，按工作簿的打印设置分页
        $doc.Close($false)
        Write-Output "{""ok"":true,""engine"":""Excel""}"
    }
}
finally {
    Release $doc
    if ($app) {
        # 只关自己开的：之前没有这个程序在跑，或程序里已经没有别的文件
        $left = switch ($kind) { 'word' { $app.Documents.Count } 'ppt' { $app.Presentations.Count } default { $app.Workbooks.Count } }
        if ($had -eq 0 -or $left -eq 0) { $app.Quit() }
        Release $app
    }
    [GC]::Collect(); [GC]::WaitForPendingFinalizers()
}
