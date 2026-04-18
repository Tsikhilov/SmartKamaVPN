$ProdHost = "72.56.100.45"
$ProdUser = "root"
$ProdPassword = "szFt1PugQ-5Hy-"
$RuHost = "72.56.252.250"
$RuUser = "root"
$RuPassword = "cLcZG1HbEEYG?^"
$RuHostKey = "SHA256:8LZEhB2P43iXuWObTGQuoZaGBFrzWLV7fDx8CXbm9R4"
$Plink = "C:\Program Files\PuTTY\plink.exe"
$Pscp = "C:\Program Files\PuTTY\pscp.exe"

function Invoke-ProdSSH {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command
    )
    & $Plink -batch -pw $ProdPassword "$ProdUser@${ProdHost}" $Command
}

function Copy-ToProd {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LocalPath,
        [Parameter(Mandatory = $true)]
        [string]$RemotePath
    )
    & $Pscp -batch -pw $ProdPassword $LocalPath "$ProdUser@${ProdHost}:$RemotePath"
}

function Invoke-RuSSH {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command
    )
    & $Plink -batch -pw $RuPassword -hostkey $RuHostKey "$RuUser@${RuHost}" $Command
}

function Copy-ToRu {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LocalPath,
        [Parameter(Mandatory = $true)]
        [string]$RemotePath
    )
    & $Pscp -batch -pw $RuPassword -hostkey $RuHostKey $LocalPath "$RuUser@${RuHost}:$RemotePath"
}

function Publish-RuServer {
    <#
    .SYNOPSIS
        Deploy network/transport optimization scripts to the RU Moscow node (72.56.252.250).
        Bot and shortlink run only on the NL server; RU node only needs sysctl tuning.
    #>
    param(
        [switch]$ApplySysctl
    )
    # Ensure destination directory exists before copying
    Invoke-RuSSH 'mkdir -p /opt/SmartKamaVPN/scripts'
    Copy-ToRu "scripts/server_optimize_mobile_transport.py" "/opt/SmartKamaVPN/scripts/server_optimize_mobile_transport.py"
    Copy-ToRu "scripts/server_diagnose_mobile.py" "/opt/SmartKamaVPN/scripts/server_diagnose_mobile.py"
    if ($ApplySysctl) {
        $cmd = 'cd /opt/SmartKamaVPN; python3 scripts/server_optimize_mobile_transport.py --sysctl-only 2>&1 | tail -20; echo === ru-sysctl-done ==='
        Invoke-RuSSH $cmd
    }
    Invoke-RuSSH 'echo "=== RU node services ==="; for svc in marzban-node marzban xray; do echo "$svc: $(systemctl is-active $svc 2>/dev/null || echo n/a)"; done; echo "=== done ==="'
}

function Convert-ToShellArg {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )
    $replacement = "'" + '"' + "'" + '"' + "'"
    $escaped = $Value.Replace("'", $replacement)
    return "'" + $escaped + "'"
}

function Publish-ProdBot {
    Copy-ToProd "UserBot/bot.py" "/opt/SmartKamaVPN/UserBot/bot.py"
    Copy-ToProd "UserBot/markups.py" "/opt/SmartKamaVPN/UserBot/markups.py"
    Copy-ToProd "UserBot/Json/buttons.json" "/opt/SmartKamaVPN/UserBot/Json/buttons.json"
    Copy-ToProd "UserBot/Json/messages.json" "/opt/SmartKamaVPN/UserBot/Json/messages.json"
    Copy-ToProd "AdminBot/bot.py" "/opt/SmartKamaVPN/AdminBot/bot.py"
    Copy-ToProd "AdminBot/markups.py" "/opt/SmartKamaVPN/AdminBot/markups.py"
    Copy-ToProd "AdminBot/templates.py" "/opt/SmartKamaVPN/AdminBot/templates.py"
    Copy-ToProd "AdminBot/Json/buttons.json" "/opt/SmartKamaVPN/AdminBot/Json/buttons.json"
    Copy-ToProd "AdminBot/Json/messages.json" "/opt/SmartKamaVPN/AdminBot/Json/messages.json"
    Copy-ToProd "Utils/api.py" "/opt/SmartKamaVPN/Utils/api.py"
    Copy-ToProd "Utils/utils.py" "/opt/SmartKamaVPN/Utils/utils.py"
    Copy-ToProd "Utils/marzban_api.py" "/opt/SmartKamaVPN/Utils/marzban_api.py"
    Copy-ToProd "scripts/shortlink_redirect.py" "/opt/SmartKamaVPN/scripts/shortlink_redirect.py"
    Copy-ToProd "scripts/server_ops_guard.py" "/opt/SmartKamaVPN/scripts/server_ops_guard.py"
    Copy-ToProd "scripts/server_autotune_stack.py" "/opt/SmartKamaVPN/scripts/server_autotune_stack.py"
    Copy-ToProd "scripts/server_install_autotune_timer.py" "/opt/SmartKamaVPN/scripts/server_install_autotune_timer.py"
    Copy-ToProd "scripts/server_telegram_selfcheck.py" "/opt/SmartKamaVPN/scripts/server_telegram_selfcheck.py"
    Copy-ToProd "scripts/check_userbot_callback_coverage.py" "/opt/SmartKamaVPN/scripts/check_userbot_callback_coverage.py"
    Copy-ToProd "scripts/selfcheck_api.py" "/opt/SmartKamaVPN/scripts/selfcheck_api.py"
    Copy-ToProd "scripts/selfcheck_marzban_api.py" "/opt/SmartKamaVPN/scripts/selfcheck_marzban_api.py"
    Copy-ToProd "scripts/server_set_panel_provider.py" "/opt/SmartKamaVPN/scripts/server_set_panel_provider.py"
    Copy-ToProd "scripts/server_add_direct_inbound.py" "/opt/SmartKamaVPN/scripts/server_add_direct_inbound.py"
    Copy-ToProd "scripts/server_optimize_mobile_transport.py" "/opt/SmartKamaVPN/scripts/server_optimize_mobile_transport.py"
    Copy-ToProd "scripts/server_diagnose_mobile.py" "/opt/SmartKamaVPN/scripts/server_diagnose_mobile.py"
    Copy-ToProd "scripts/server_signal_proxy.py" "/opt/SmartKamaVPN/scripts/server_signal_proxy.py"
    Copy-ToProd "Database/dbManager.py" "/opt/SmartKamaVPN/Database/dbManager.py"
    Copy-ToProd "Cronjob/reminder.py" "/opt/SmartKamaVPN/Cronjob/reminder.py"
    Copy-ToProd "Cronjob/payment_check.py" "/opt/SmartKamaVPN/Cronjob/payment_check.py"
    Copy-ToProd "Utils/cryptopay.py" "/opt/SmartKamaVPN/Utils/cryptopay.py"
    Copy-ToProd "Utils/yookassa.py" "/opt/SmartKamaVPN/Utils/yookassa.py"
    Copy-ToProd "crontab.py" "/opt/SmartKamaVPN/crontab.py"
    Copy-ToProd "config.py" "/opt/SmartKamaVPN/config.py"
    Copy-ToProd "autotune-policy.json" "/opt/SmartKamaVPN/autotune-policy.json"

    $cmd = @'
set -e;
cd /opt/SmartKamaVPN;
.venv/bin/python -m py_compile UserBot/bot.py UserBot/markups.py Utils/api.py Utils/marzban_api.py Utils/cryptopay.py Database/dbManager.py config.py crontab.py Cronjob/payment_check.py scripts/shortlink_redirect.py scripts/server_telegram_selfcheck.py scripts/check_userbot_callback_coverage.py scripts/selfcheck_api.py scripts/selfcheck_marzban_api.py scripts/server_set_panel_provider.py;
.venv/bin/python scripts/check_userbot_callback_coverage.py --markups UserBot/markups.py --bot UserBot/bot.py;
.venv/bin/python -c 'import config, subprocess, sys; p=str(getattr(config,"PANEL_PROVIDER","3xui")).strip().lower(); print("selfcheck_provider=" + p); s=["scripts/selfcheck_marzban_api.py"] if p=="marzban" else ["scripts/selfcheck_api.py"]; raise SystemExit(subprocess.call([sys.executable] + s))';
systemctl restart smartkamavpn smartkama-shortlink;
systemctl is-active smartkamavpn;
systemctl is-active smartkama-shortlink
'@
    $cmd = ($cmd -replace "`r", "" -replace "`n", " ")
    Invoke-ProdSSH $cmd
}

function Test-ProdTelegram {
    Invoke-ProdSSH "set -e; cd /opt/SmartKamaVPN; /opt/SmartKamaVPN/.venv/bin/python scripts/server_telegram_selfcheck.py --check-client --no-send-test-message"
}

function Test-ProdSubscriptionClientMenu {
    $cmd = @'
set -e;
echo '== code markers ==';
grep -n 'smartkamavpn_conf_happ' /opt/SmartKamaVPN/UserBot/bot.py;
grep -n 'def sk_params_markup' /opt/SmartKamaVPN/UserBot/markups.py;
grep -n 'Happ / V2RayTun' /opt/SmartKamaVPN/UserBot/markups.py;
echo '== callback handlers ==';
grep -n 'elif key == "conf_sub_url"' /opt/SmartKamaVPN/UserBot/bot.py;
grep -n 'elif key == "conf_sub_auto"' /opt/SmartKamaVPN/UserBot/bot.py;
grep -n 'elif key == "conf_clash"' /opt/SmartKamaVPN/UserBot/bot.py;
grep -n 'elif key == "conf_hiddify"' /opt/SmartKamaVPN/UserBot/bot.py;
grep -n 'elif key == "conf_sub_sing_box"' /opt/SmartKamaVPN/UserBot/bot.py;
grep -n 'elif key == "smartkamavpn_conf_happ"' /opt/SmartKamaVPN/UserBot/bot.py;
echo '== services ==';
        systemctl is-active smartkamavpn smartkama-shortlink nginx;
        systemctl is-active x-ui || true;
echo '== callback coverage ==';
/opt/SmartKamaVPN/.venv/bin/python /opt/SmartKamaVPN/scripts/check_userbot_callback_coverage.py --markups /opt/SmartKamaVPN/UserBot/markups.py --bot /opt/SmartKamaVPN/UserBot/bot.py;
echo '== recent errors ==';
journalctl -u smartkamavpn -n 200 --no-pager | egrep -i 'traceback|error|exception|failed' || true
'@
    $cmd = ($cmd -replace "`r", "" -replace "`n", " ")
    Invoke-ProdSSH $cmd
}

function Invoke-ProdGuard {
    param(
        [ValidateSet("diagnose", "autofix", "smoke", "all")]
        [string]$Mode = "all",
        [string]$SubId = ""
    )

    $subArg = ""
    if ($SubId -and $SubId.Trim().Length -gt 0) {
        $subArg = " --sub-id $SubId"
    }

    $cmd = "set -e; cd /opt/SmartKamaVPN; /opt/SmartKamaVPN/.venv/bin/python scripts/server_ops_guard.py --mode $Mode$subArg"
    Invoke-ProdSSH $cmd
}

function Invoke-ProdDeployAndGuard {
    param(
        [string]$SubId = ""
    )

    Publish-ProdBot
    Test-ProdSubscriptionClientMenu
    Test-ProdTelegram
    Invoke-ProdGuard -Mode "all" -SubId $SubId
}

function Set-ProdPanelProvider {
    param(
        [ValidateSet("3xui", "marzban")]
        [string]$Provider = "marzban",
        [string]$MarzbanPanelUrl,
        [string]$MarzbanUsername,
        [string]$MarzbanPassword,
        [string]$MarzbanAccessToken,
        [string]$MarzbanTlsVerify,
        [string]$MarzbanInboundTags
    )

    $parts = @(
        "set -e;",
        "cd /opt/SmartKamaVPN;",
        "/opt/SmartKamaVPN/.venv/bin/python scripts/server_set_panel_provider.py",
        "--provider $Provider"
    )

    if ($Provider -eq "marzban") {
        if ($PSBoundParameters.ContainsKey('MarzbanPanelUrl') -and $MarzbanPanelUrl) {
            $parts += "--marzban-panel-url $(Convert-ToShellArg $MarzbanPanelUrl)"
        }
        if ($PSBoundParameters.ContainsKey('MarzbanUsername')) {
            $parts += "--marzban-username $(Convert-ToShellArg $MarzbanUsername)"
        }
        if ($PSBoundParameters.ContainsKey('MarzbanPassword')) {
            $parts += "--marzban-password $(Convert-ToShellArg $MarzbanPassword)"
        }
        if ($PSBoundParameters.ContainsKey('MarzbanAccessToken')) {
            $parts += "--marzban-access-token $(Convert-ToShellArg $MarzbanAccessToken)"
        }
        if ($PSBoundParameters.ContainsKey('MarzbanTlsVerify')) {
            if ($MarzbanTlsVerify -notin @("true", "false")) {
                throw "MarzbanTlsVerify must be 'true' or 'false'"
            }
            $parts += "--marzban-tls-verify $MarzbanTlsVerify"
        }
        if ($PSBoundParameters.ContainsKey('MarzbanInboundTags')) {
            $parts += "--marzban-inbound-tags $(Convert-ToShellArg $MarzbanInboundTags)"
        }
    }

    $cmd = $parts -join " "
    Invoke-ProdSSH $cmd
}

function Get-ProdPanelProvider {
    $cmd = "set -e; cd /opt/SmartKamaVPN; /opt/SmartKamaVPN/.venv/bin/python scripts/server_set_panel_provider.py --show"
    Invoke-ProdSSH $cmd
}

function Invoke-ProdMarzbanTurnkey {
    param(
        [string]$MarzbanPanelUrl,
        [string]$MarzbanUsername,
        [string]$MarzbanPassword,
        [string]$MarzbanAccessToken,
        [string]$MarzbanTlsVerify,
        [string]$MarzbanInboundTags,
        [string]$SubId = "",
        [ValidateSet("diagnose", "autofix", "smoke", "all")]
        [string]$GuardMode = "all",
        [switch]$SkipAutotune
    )

    Publish-ProdBot
    Get-ProdPanelProvider

    $setParams = @{
        Provider = "marzban"
    }
    foreach ($name in @("MarzbanPanelUrl", "MarzbanUsername", "MarzbanPassword", "MarzbanAccessToken", "MarzbanTlsVerify", "MarzbanInboundTags")) {
        if ($PSBoundParameters.ContainsKey($name)) {
            $setParams[$name] = $PSBoundParameters[$name]
        }
    }
    Set-ProdPanelProvider @setParams
    Get-ProdPanelProvider

    Test-ProdSubscriptionClientMenu
    Test-ProdTelegram
    Invoke-ProdGuard -Mode "all" -SubId $SubId
    if (-not $SkipAutotune) {
        Invoke-ProdAutotune -GuardMode $GuardMode
    }
}

function Invoke-ProdAutotune {
    param(
        [ValidateSet("diagnose", "autofix", "smoke", "all")]
        [string]$GuardMode = "all"
    )

    $cmd = "set -e; cd /opt/SmartKamaVPN; /opt/SmartKamaVPN/.venv/bin/python scripts/server_autotune_stack.py --full --guard-mode $GuardMode"
    Invoke-ProdSSH $cmd
}

function Install-ProdAutotuneTimer {
    param(
        [ValidateSet("calendar", "interval")]
        [string]$ScheduleMode = "interval",
        [string]$OnCalendar = "*-*-* 04,16:00:00",
        [string]$OnBootSec = "10m",
        [string]$OnUnitActiveSec = "30m",
        [ValidateSet("diagnose", "autofix", "smoke", "all")]
        [string]$GuardMode = "smoke"
    )

    $cmd = "set -e; cd /opt/SmartKamaVPN; /opt/SmartKamaVPN/.venv/bin/python scripts/server_install_autotune_timer.py --schedule-mode '$ScheduleMode' --guard-mode '$GuardMode'"
    if ($ScheduleMode -eq "calendar") {
        $cmd += " --on-calendar '$OnCalendar'"
    } else {
        $cmd += " --on-boot-sec '$OnBootSec' --on-unit-active-sec '$OnUnitActiveSec'"
    }
    Invoke-ProdSSH $cmd
}

function Install-ProdCron {
    Copy-ToProd "scripts/server_install_cron.py" "/opt/SmartKamaVPN/scripts/server_install_cron.py"
    Copy-ToProd "crontab.py" "/opt/SmartKamaVPN/crontab.py"
    $cmd = "set -e; cd /opt/SmartKamaVPN; /opt/SmartKamaVPN/.venv/bin/python scripts/server_install_cron.py --install"
    Invoke-ProdSSH $cmd
}

function Show-ProdCron {
    Invoke-ProdSSH "crontab -l"
}

