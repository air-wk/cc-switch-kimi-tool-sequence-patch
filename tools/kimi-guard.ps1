param([ValidateSet('status','enable','disable','start','stop','restart')][string]$Action = 'status')
$ErrorActionPreference = 'Stop'
$python = 'G:\Anaconda\install\python.exe'
$script = Join-Path $env:USERPROFILE '.codex\tools\kimi_responses_guard.py'
$routes = @(
    [pscustomobject]@{Name='Volcengine';Port=4100;Upstream='https://ark.cn-beijing.volces.com/api/coding/v3'},
    [pscustomobject]@{Name='Kimi Official';Port=4101;Upstream='https://api.kimi.com/coding/v1'},
    [pscustomobject]@{Name='OpenCode';Port=4102;Upstream='https://opencode.ai/zen/go/v1'}
)
$results = foreach ($route in $routes) {
    $cliArgs = @($script,$Action,'--port',[string]$route.Port)
    if ($Action -in @('start','restart','enable','disable')) {
        $cliArgs += @('--upstream',$route.Upstream)
    }
    $raw = & $python @cliArgs
    if ($LASTEXITCODE -ne 0) { throw "$($route.Name) control command failed" }
    [pscustomobject]@{Name=$route.Name;Port=$route.Port;Status=($raw | ConvertFrom-Json)}
}
$results | ConvertTo-Json -Depth 5
