# speech_server.ps1
# Line-oriented SAPI speech server driven over stdin/stdout.
#
# Protocol (UTF-8, one command per line, fields separated by '|'):
#   VOICES                     -> VOICES|<name>|<name>|...
#   VOICE|<b64 name>           -> OK|VOICE
#   RATE|<-10..10>             -> OK|RATE
#   VOLUME|<0..100>            -> OK|VOLUME
#   SPEAK|<b64 utf8 text>      -> OK|SPEAK
#   PAUSE                      -> OK|PAUSE
#   RESUME                     -> OK|RESUME
#   STOP                       -> OK|STOP
#   STATE                      -> STATE|<Ready|Speaking|Paused>|<0|1 prompt done>
#   SAVE|<b64 path>|<b64 text> -> OK|SAVE
#   QUIT                       -> exits
# Any failure replies ERR|<message>.
#
# Text is base64-encoded so newlines, pipes and non-ASCII survive the line protocol.

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8

Add-Type -AssemblyName System.Speech

$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SetOutputToDefaultAudioDevice()

$prompt = $null

function Send-Line([string]$msg) {
    [Console]::Out.WriteLine($msg)
    [Console]::Out.Flush()
}

function ConvertFrom-B64([string]$s) {
    [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($s))
}

Send-Line 'READY|1'

$running = $true
while ($running -and $null -ne ($line = [Console]::In.ReadLine())) {
    if (-not $line) { continue }
    $parts = $line.Split('|')
    $cmd = $parts[0].ToUpperInvariant()
    try {
        switch ($cmd) {
            'VOICES' {
                $names = @()
                foreach ($v in $synth.GetInstalledVoices()) {
                    if ($v.Enabled) { $names += $v.VoiceInfo.Name }
                }
                Send-Line ('VOICES|' + ($names -join '|'))
            }
            'VOICE' {
                $synth.SelectVoice((ConvertFrom-B64 $parts[1]))
                Send-Line 'OK|VOICE'
            }
            'RATE' {
                $synth.Rate = [int]$parts[1]
                Send-Line 'OK|RATE'
            }
            'VOLUME' {
                $synth.Volume = [int]$parts[1]
                Send-Line 'OK|VOLUME'
            }
            'SPEAK' {
                # Resume first: cancelling while paused leaves the engine wedged.
                if ($synth.State -eq 'Paused') { $synth.Resume() }
                $synth.SpeakAsyncCancelAll()
                $prompt = $synth.SpeakAsync((ConvertFrom-B64 $parts[1]))
                Send-Line 'OK|SPEAK'
            }
            'PAUSE' {
                if ($synth.State -eq 'Speaking') { $synth.Pause() }
                Send-Line 'OK|PAUSE'
            }
            'RESUME' {
                if ($synth.State -eq 'Paused') { $synth.Resume() }
                Send-Line 'OK|RESUME'
            }
            'STOP' {
                if ($synth.State -eq 'Paused') { $synth.Resume() }
                $synth.SpeakAsyncCancelAll()
                $prompt = $null
                Send-Line 'OK|STOP'
            }
            'STATE' {
                $done = if ($null -eq $prompt) { 1 } elseif ($prompt.IsCompleted) { 1 } else { 0 }
                Send-Line ('STATE|' + $synth.State + '|' + $done)
            }
            'SAVE' {
                $path = ConvertFrom-B64 $parts[1]
                $text = ConvertFrom-B64 $parts[2]
                if ($synth.State -eq 'Paused') { $synth.Resume() }
                $synth.SpeakAsyncCancelAll()
                $prompt = $null
                try {
                    $synth.SetOutputToWaveFile($path)
                    $synth.Speak($text)
                }
                finally {
                    $synth.SetOutputToDefaultAudioDevice()
                }
                Send-Line 'OK|SAVE'
            }
            'QUIT' {
                $running = $false
            }
            default {
                Send-Line ('ERR|unknown command: ' + $cmd)
            }
        }
    }
    catch {
        Send-Line ('ERR|' + ($_.Exception.Message -replace '[\r\n]+', ' '))
    }
}

try { $synth.SpeakAsyncCancelAll() } catch { }
$synth.Dispose()
