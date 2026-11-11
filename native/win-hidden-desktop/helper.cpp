// dsh-blender-hidden-desktop: launch one Blender GUI process on a private,
// hidden Win32 desktop so virtual-gui sessions stay invisible to the user.
//
// Protocol (one JSON object per line on stdout):
//   ready  {"event":"ready","helper":<pid>,"blender":<pid>,"display":"...","exe":"..."}
//   error  {"event":"error","stage":"...","code":"...","message":"...","win32":<code>}
// After the ready line the child's stdout and stderr are forwarded byte for
// byte. The helper exit code is the child exit code; helper failures use 2-9.
//
// Integrity labels: Windows applies the image file's mandatory label to the
// child token, so a Low-labelled Blender (for example an install extracted by a
// sandboxed tool) runs at Low. A hidden desktop is created at the helper's own
// integrity level, and a Low process cannot write up to a Medium desktop; the
// child then fails in DLL initialization with 0xC0000142. The helper lowers the
// hidden desktop's label to match min(helper, child image) before launch.
//
// This launcher never falls back to the default desktop: when the private
// desktop, the job object, or the pipes cannot be created it reports the
// failure and exits without starting Blender.

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <aclapi.h>
#include <accctrl.h>
#include <sddl.h>

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#if defined(__has_include)
#if __has_include("build-info.h")
#include "build-info.h"
#endif
#endif

#ifndef DSH_HELPER_VERSION
#define DSH_HELPER_VERSION "dev"
#endif
#ifndef DSH_HELPER_COMMIT
#define DSH_HELPER_COMMIT "unknown"
#endif
#ifndef DSH_HELPER_PROTOCOL
#define DSH_HELPER_PROTOCOL 1
#endif

namespace {

constexpr int kExitBadArgs = 2;
constexpr int kExitDesktopFailed = 3;
constexpr int kExitJobFailed = 4;
constexpr int kExitSpawnFailed = 5;
constexpr int kExitResumeFailed = 6;
constexpr int kExitInternal = 9;

const wchar_t kUsage[] =
    L"dsh-blender-hidden-desktop: run one Blender GUI process on a private hidden Windows desktop.\n"
    L"\n"
    L"Usage:\n"
    L"  dsh-blender-hidden-desktop --exe <blender.exe> [--desktop <name>] [--cwd <dir>]\n"
    L"      [--parent-pid <pid>] [-- <blender args...>]\n"
    L"  dsh-blender-hidden-desktop --version\n"
    L"  dsh-blender-hidden-desktop --self-test\n"
    L"  dsh-blender-hidden-desktop --help\n";

void writeAll(HANDLE handle, const char* data, std::size_t size) {
  std::size_t offset = 0;
  while (offset < size) {
    const std::size_t remaining = size - offset;
    const DWORD chunk = static_cast<DWORD>(remaining > 0x7fffffffu ? 0x7fffffffu : remaining);
    DWORD written = 0;
    if (!WriteFile(handle, data + offset, chunk, &written, nullptr) || written == 0) {
      return;
    }
    offset += written;
  }
}

void writeLine(HANDLE handle, const std::string& line) {
  const std::string output = line + "\n";
  writeAll(handle, output.data(), output.size());
}

std::string utf8(const std::wstring& text) {
  if (text.empty()) {
    return {};
  }
  const int needed = WideCharToMultiByte(CP_UTF8, 0, text.data(), static_cast<int>(text.size()),
                                         nullptr, 0, nullptr, nullptr);
  if (needed <= 0) {
    return {};
  }
  std::string output(static_cast<std::size_t>(needed), '\0');
  WideCharToMultiByte(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), output.data(), needed,
                      nullptr, nullptr);
  return output;
}

std::string jsonEscape(const std::string& value) {
  std::string output;
  output.reserve(value.size() + 8);
  for (const unsigned char character : value) {
    switch (character) {
      case '"':
        output += "\\\"";
        break;
      case '\\':
        output += "\\\\";
        break;
      case '\b':
        output += "\\b";
        break;
      case '\f':
        output += "\\f";
        break;
      case '\n':
        output += "\\n";
        break;
      case '\r':
        output += "\\r";
        break;
      case '\t':
        output += "\\t";
        break;
      default:
        if (character < 0x20) {
          char buffer[8] = {};
          std::snprintf(buffer, sizeof(buffer), "\\u%04x", static_cast<unsigned>(character));
          output += buffer;
        } else {
          output.push_back(static_cast<char>(character));
        }
        break;
    }
  }
  return output;
}

void logLine(const std::string& message) {
  const std::string output = "[dsh-helper] " + message + "\n";
  writeAll(GetStdHandle(STD_ERROR_HANDLE), output.data(), output.size());
}

int failEvent(const std::string& stage, const std::string& code, const std::wstring& message,
              DWORD win32, int exitCode) {
  std::string output = "{\"event\":\"error\",\"stage\":\"" + jsonEscape(stage) +
                       "\",\"code\":\"" + jsonEscape(code) + "\",\"message\":\"" +
                       jsonEscape(utf8(message)) + "\"";
  if (win32 != 0) {
    output += ",\"win32\":" + std::to_string(win32);
  }
  output += "}";
  writeLine(GetStdHandle(STD_OUTPUT_HANDLE), output);
  logLine(code + ": " + utf8(message));
  return exitCode;
}

void closeHandle(HANDLE& handle) {
  if (handle != nullptr && handle != INVALID_HANDLE_VALUE) {
    CloseHandle(handle);
  }
  handle = nullptr;
}

struct Options {
  std::wstring exe;
  std::wstring desktop;
  std::wstring cwd;
  DWORD parentPid = 0;
  std::vector<std::wstring> childArgs;
  bool version = false;
  bool selfTest = false;
  bool help = false;
};

bool parseArgs(int argc, wchar_t** argv, Options& options, std::wstring& error) {
  for (int index = 1; index < argc; ++index) {
    const std::wstring argument = argv[index];
    if (argument == L"--version") {
      options.version = true;
    } else if (argument == L"--self-test") {
      options.selfTest = true;
    } else if (argument == L"--help" || argument == L"-h") {
      options.help = true;
    } else if (argument == L"--exe") {
      if (++index >= argc) {
        error = L"--exe needs a value";
        return false;
      }
      options.exe = argv[index];
    } else if (argument == L"--desktop") {
      if (++index >= argc) {
        error = L"--desktop needs a value";
        return false;
      }
      options.desktop = argv[index];
    } else if (argument == L"--cwd") {
      if (++index >= argc) {
        error = L"--cwd needs a value";
        return false;
      }
      options.cwd = argv[index];
    } else if (argument == L"--parent-pid") {
      if (++index >= argc) {
        error = L"--parent-pid needs a value";
        return false;
      }
      wchar_t* end = nullptr;
      const unsigned long value = std::wcstoul(argv[index], &end, 10);
      if (end == argv[index] || *end != L'\0') {
        error = L"--parent-pid must be a positive integer";
        return false;
      }
      options.parentPid = static_cast<DWORD>(value);
    } else if (argument == L"--") {
      for (++index; index < argc; ++index) {
        options.childArgs.push_back(argv[index]);
      }
      break;
    } else {
      error = L"unknown argument: " + argument;
      return false;
    }
  }
  if (options.help || options.version || options.selfTest) {
    return true;
  }
  if (options.exe.empty()) {
    error = L"--exe is required";
    return false;
  }
  return true;
}

// Quote one argv element with the Windows CommandLineToArgvW rules so Blender
// sees exactly the arguments the caller passed.
std::wstring quoteArg(const std::wstring& argument) {
  if (!argument.empty() && argument.find_first_of(L" \t\n\v\"") == std::wstring::npos) {
    return argument;
  }
  std::wstring output = L"\"";
  for (std::size_t index = 0; index < argument.size();) {
    std::size_t backslashes = 0;
    while (index < argument.size() && argument[index] == L'\\') {
      ++index;
      ++backslashes;
    }
    if (index == argument.size()) {
      output.append(backslashes * 2, L'\\');
      break;
    }
    if (argument[index] == L'"') {
      output.append(backslashes * 2 + 1, L'\\');
      output.push_back(L'"');
      ++index;
    } else {
      output.append(backslashes, L'\\');
      output.push_back(argument[index]);
      ++index;
    }
  }
  output.push_back(L'"');
  return output;
}

bool validDesktopName(const std::wstring& name) {
  if (name.empty() || name.size() > 180) {
    return false;
  }
  for (const wchar_t character : name) {
    if (character < 0x20 || character == L'\\' || character == L'/') {
      return false;
    }
  }
  return true;
}

bool createPipePair(HANDLE& read, HANDLE& write) {
  SECURITY_ATTRIBUTES attributes{};
  attributes.nLength = sizeof(attributes);
  attributes.bInheritHandle = TRUE;
  if (!CreatePipe(&read, &write, &attributes, 0)) {
    return false;
  }
  if (!SetHandleInformation(read, HANDLE_FLAG_INHERIT, 0)) {
    return false;
  }
  return true;
}

HANDLE createNulInput() {
  SECURITY_ATTRIBUTES attributes{};
  attributes.nLength = sizeof(attributes);
  attributes.bInheritHandle = TRUE;
  return CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, &attributes,
                     OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
}

HANDLE createJob() {
  HANDLE job = CreateJobObjectW(nullptr, nullptr);
  if (job == nullptr) {
    return nullptr;
  }
  JOBOBJECT_EXTENDED_LIMIT_INFORMATION information{};
  information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
  if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, &information,
                               sizeof(information))) {
    CloseHandle(job);
    return nullptr;
  }
  return job;
}


// Read the mandatory label RID from a security descriptor's SACL.
DWORD labelRidFromDescriptor(PSECURITY_DESCRIPTOR descriptor) {
  if (descriptor == nullptr) {
    return 0;
  }
  PACL sacl = nullptr;
  BOOL present = FALSE;
  BOOL defaulted = FALSE;
  if (!GetSecurityDescriptorSacl(descriptor, &present, &sacl, &defaulted) || !present ||
      sacl == nullptr) {
    return 0;
  }
  ACL_SIZE_INFORMATION information{};
  if (!GetAclInformation(sacl, &information, sizeof(information), AclSizeInformation)) {
    return 0;
  }
  for (DWORD index = 0; index < information.AceCount; ++index) {
    LPVOID ace = nullptr;
    if (!GetAce(sacl, index, &ace) || ace == nullptr) {
      continue;
    }
    auto* mandatory = static_cast<SYSTEM_MANDATORY_LABEL_ACE*>(ace);
    if (mandatory->Header.AceType != SYSTEM_MANDATORY_LABEL_ACE_TYPE) {
      continue;
    }
    PSID sid = reinterpret_cast<PSID>(&mandatory->SidStart);
    const DWORD count = *GetSidSubAuthorityCount(sid);
    if (count == 0) {
      return 0;
    }
    return *GetSidSubAuthority(sid, count - 1);
  }
  return 0;
}

DWORD currentIntegrityRid() {
  HANDLE token = nullptr;
  if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) {
    return SECURITY_MANDATORY_MEDIUM_RID;
  }
  DWORD size = 0;
  GetTokenInformation(token, TokenIntegrityLevel, nullptr, 0, &size);
  std::vector<unsigned char> buffer(size);
  DWORD rid = SECURITY_MANDATORY_MEDIUM_RID;
  if (size != 0 &&
      GetTokenInformation(token, TokenIntegrityLevel, buffer.data(), size, &size)) {
    auto* label = reinterpret_cast<TOKEN_MANDATORY_LABEL*>(buffer.data());
    DWORD subAuthorities = 0;
    if (label->Label.Sid != nullptr) {
      subAuthorities = *GetSidSubAuthorityCount(label->Label.Sid);
    }
    if (subAuthorities > 0) {
      rid = *GetSidSubAuthority(label->Label.Sid, subAuthorities - 1);
    }
  }
  CloseHandle(token);
  return rid;
}

// An image without an explicit label runs at Medium by default.
DWORD imageIntegrityRid(const std::wstring& path) {
  PSECURITY_DESCRIPTOR descriptor = nullptr;
  const DWORD status = GetNamedSecurityInfoW(path.c_str(), SE_FILE_OBJECT,
                                             LABEL_SECURITY_INFORMATION, nullptr, nullptr, nullptr,
                                             nullptr, &descriptor);
  if (status != ERROR_SUCCESS) {
    return SECURITY_MANDATORY_MEDIUM_RID;
  }
  const DWORD rid = labelRidFromDescriptor(descriptor);
  LocalFree(descriptor);
  return rid != 0 ? rid : SECURITY_MANDATORY_MEDIUM_RID;
}

DWORD desktopIntegrityRid(HDESK desktop) {
  PSECURITY_DESCRIPTOR descriptor = nullptr;
  const DWORD status =
      GetSecurityInfo(reinterpret_cast<HANDLE>(desktop), SE_WINDOW_OBJECT,
                      LABEL_SECURITY_INFORMATION, nullptr, nullptr, nullptr, nullptr, &descriptor);
  if (status != ERROR_SUCCESS) {
    return 0;
  }
  const DWORD rid = labelRidFromDescriptor(descriptor);
  LocalFree(descriptor);
  return rid;
}

const wchar_t* labelSddl(DWORD rid) {
  switch (rid) {
    case SECURITY_MANDATORY_LOW_RID:
      return L"S:(ML;;NW;;;LW)";
    case SECURITY_MANDATORY_MEDIUM_RID:
      return L"S:(ML;;NW;;;ME)";
    case SECURITY_MANDATORY_HIGH_RID:
      return L"S:(ML;;NW;;;HI)";
    case SECURITY_MANDATORY_SYSTEM_RID:
      return L"S:(ML;;NW;;;SI)";
    default:
      return nullptr;
  }
}

// Lowering a label on an object the helper owns is allowed. Raising, or a rid
// the helper cannot express, is left alone: the desktop simply keeps the
// helper's own integrity level, which is what the child gets anyway.
bool lowerDesktopLabel(HDESK desktop, DWORD rid) {
  const wchar_t* sddl = labelSddl(rid);
  if (sddl == nullptr) {
    return true;
  }
  PSECURITY_DESCRIPTOR descriptor = nullptr;
  if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, SDDL_REVISION_1, &descriptor,
                                                            nullptr)) {
    return false;
  }
  PACL sacl = nullptr;
  BOOL present = FALSE;
  BOOL defaulted = FALSE;
  const bool haveSacl =
      GetSecurityDescriptorSacl(descriptor, &present, &sacl, &defaulted) && present;
  DWORD status = ERROR_INVALID_SECURITY_DESCR;
  if (haveSacl) {
    status = SetSecurityInfo(reinterpret_cast<HANDLE>(desktop), SE_WINDOW_OBJECT,
                             LABEL_SECURITY_INFORMATION, nullptr, nullptr, nullptr, sacl);
  }
  LocalFree(descriptor);
  return status == ERROR_SUCCESS;
}

struct PipePump {
  HANDLE source = nullptr;
  HANDLE destination = nullptr;
};

DWORD WINAPI pumpThreadProc(LPVOID parameter) {
  auto* pump = static_cast<PipePump*>(parameter);
  char buffer[8192];
  for (;;) {
    DWORD read = 0;
    if (!ReadFile(pump->source, buffer, static_cast<DWORD>(sizeof(buffer)), &read, nullptr) ||
        read == 0) {
      break;
    }
    writeAll(pump->destination, buffer, read);
  }
  CloseHandle(pump->source);
  delete pump;
  return 0;
}

HANDLE startPump(HANDLE source, HANDLE destination) {
  auto* pump = new PipePump{source, destination};
  HANDLE thread = CreateThread(nullptr, 0, pumpThreadProc, pump, 0, nullptr);
  if (thread == nullptr) {
    CloseHandle(source);
    delete pump;
  }
  return thread;
}

void stopPump(HANDLE& thread) {
  if (thread == nullptr) {
    return;
  }
  CancelSynchronousIo(thread);
  WaitForSingleObject(thread, 2000);
  CloseHandle(thread);
  thread = nullptr;
}

int runSelfTest(HANDLE out) {
  std::vector<std::pair<std::string, bool>> checks;
  const auto add = [&checks](const char* name, bool ok) { checks.emplace_back(name, ok); };

  {
    Options options;
    std::wstring error;
    wchar_t* argvOk[] = {const_cast<wchar_t*>(L"helper"), const_cast<wchar_t*>(L"--exe"),
                        const_cast<wchar_t*>(L"blender.exe")};
    add("parse_accepts_exe", parseArgs(3, argvOk, options, error) &&
                                 options.exe == L"blender.exe");
    Options missing;
    std::wstring missingError;
    wchar_t* argvMissing[] = {const_cast<wchar_t*>(L"helper")};
    add("parse_requires_exe", !parseArgs(1, argvMissing, missing, missingError));
    Options passthrough;
    std::wstring passthroughError;
    wchar_t* argvPass[] = {const_cast<wchar_t*>(L"helper"), const_cast<wchar_t*>(L"--exe"),
                          const_cast<wchar_t*>(L"blender.exe"), const_cast<wchar_t*>(L"--"),
                          const_cast<wchar_t*>(L"--factory-startup"),
                          const_cast<wchar_t*>(L"a b")};
    add("parse_passes_child_args",
        parseArgs(6, argvPass, passthrough, passthroughError) &&
            passthrough.childArgs.size() == 2 && passthrough.childArgs[0] == L"--factory-startup" &&
            passthrough.childArgs[1] == L"a b");
  }

  add("desktop_rejects_backslash", !validDesktopName(L"bad\\name"));
  add("desktop_rejects_slash", !validDesktopName(L"bad/name"));
  add("desktop_rejects_empty", !validDesktopName(L""));
  add("desktop_accepts_normal", validDesktopName(L"dsh-vgui-123"));

  add("quote_plain", quoteArg(L"C:\\tools\\blender.exe") == L"C:\\tools\\blender.exe");
  add("quote_space",
      quoteArg(L"C:\\Program Files\\blender.exe") == L"\"C:\\Program Files\\blender.exe\"");
  add("quote_quote", quoteArg(L"a\"b") == L"\"a\\\"b\"");
  add("json_escape_quote", jsonEscape("a\"b") == "a\\\"b");
  add("json_escape_newline", jsonEscape("\n") == "\\n");

  {
    const std::wstring name =
        L"dsh-helper-selftest-" + std::to_wstring(GetCurrentProcessId());
    HDESK desktop = CreateDesktopW(name.c_str(), nullptr, nullptr, 0, GENERIC_ALL, nullptr);
    add("create_desktop", desktop != nullptr);
    if (desktop != nullptr) {
      CloseDesktop(desktop);
    }
  }
  {
    HDESK docked = CreateDesktopW(L"bad\\name", nullptr, nullptr, 0, GENERIC_ALL, nullptr);
    add("invalid_desktop_fails", docked == nullptr);
    if (docked != nullptr) {
      CloseDesktop(docked);
    }
  }
  {
    HANDLE job = createJob();
    add("create_job", job != nullptr);
    if (job != nullptr) {
      CloseHandle(job);
    }
  }
  {
    const std::wstring labelName =
        L"dsh-helper-label-" + std::to_wstring(GetCurrentProcessId());
    HDESK desktop = CreateDesktopW(labelName.c_str(), nullptr, nullptr, 0, GENERIC_ALL, nullptr);
    bool labelOk = false;
    if (desktop != nullptr) {
      labelOk = lowerDesktopLabel(desktop, SECURITY_MANDATORY_LOW_RID) &&
                desktopIntegrityRid(desktop) == SECURITY_MANDATORY_LOW_RID;
      CloseDesktop(desktop);
    }
    add("desktop_label_low", labelOk);
  }
  add("label_mapping_low", labelSddl(SECURITY_MANDATORY_LOW_RID) != nullptr);
  add("label_mapping_unknown", labelSddl(0x1234u) == nullptr);

  bool ok = true;
  for (const auto& check : checks) {
    ok = ok && check.second;
  }
  std::string checksJson;
  for (std::size_t index = 0; index < checks.size(); ++index) {
    if (index != 0) {
      checksJson += ",";
    }
    checksJson += "{\"name\":\"" + jsonEscape(checks[index].first) + "\",\"ok\":" +
                  (checks[index].second ? "true" : "false") + "}";
  }
  const std::string output = "{\"event\":\"self-test\",\"version\":\"" +
                             jsonEscape(DSH_HELPER_VERSION) + "\",\"protocol\":" +
                             std::to_string(DSH_HELPER_PROTOCOL) + ",\"checks\":[" + checksJson +
                             "],\"ok\":" + (ok ? "true" : "false") + "}";
  writeLine(out, output);
  return ok ? 0 : 1;
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
  Options options;
  std::wstring error;
  if (!parseArgs(argc, argv, options, error)) {
    return failEvent("config", "bad_args", error, 0, kExitBadArgs);
  }

  HANDLE out = GetStdHandle(STD_OUTPUT_HANDLE);
  if (options.help) {
    writeLine(out, utf8(kUsage));
    return 0;
  }
  if (options.version) {
    writeLine(out, std::string("{\"event\":\"version\",\"name\":\"dsh-blender-hidden-desktop\",\"version\":\"") +
                       jsonEscape(DSH_HELPER_VERSION) + "\",\"commit\":\"" +
                       jsonEscape(DSH_HELPER_COMMIT) + "\",\"protocol\":" +
                       std::to_string(DSH_HELPER_PROTOCOL) + "}");
    return 0;
  }
  if (options.selfTest) {
    return runSelfTest(out);
  }

  std::wstring desktop = options.desktop;
  if (desktop.empty()) {
    desktop = L"dsh-vgui-" + std::to_wstring(GetCurrentProcessId()) + L"-" +
              std::to_wstring(GetTickCount64());
  }
  if (!validDesktopName(desktop)) {
    return failEvent("config", "bad_desktop_name",
                     L"--desktop must be 1-180 characters and contain no backslash, slash, or "
                     L"control character",
                     0, kExitBadArgs);
  }

  HDESK desktopHandle = CreateDesktopW(desktop.c_str(), nullptr, nullptr, 0, GENERIC_ALL, nullptr);
  if (desktopHandle == nullptr) {
    return failEvent("spawn", "desktop_failed", L"could not create the hidden desktop",
                     GetLastError(), kExitDesktopFailed);
  }

  // Windows lowers the child token to the image's mandatory label. A hidden
  // desktop is created at the helper's level, so a Low-labelled Blender needs a
  // Low-labelled desktop; otherwise user32 initialization fails with
  // 0xC0000142. Match min(helper, image) and fail closed if the label cannot be set.
  {
    const DWORD helperRid = currentIntegrityRid();
    const DWORD imageRid = imageIntegrityRid(options.exe);
    const DWORD desiredRid = imageRid < helperRid ? imageRid : helperRid;
    char labelLog[128] = {};
    std::snprintf(labelLog, sizeof(labelLog), "integrity helper=0x%lx image=0x%lx desired=0x%lx",
                  static_cast<unsigned long>(helperRid), static_cast<unsigned long>(imageRid),
                  static_cast<unsigned long>(desiredRid));
    logLine(labelLog);
    if (desiredRid < helperRid && !lowerDesktopLabel(desktopHandle, desiredRid)) {
      CloseDesktop(desktopHandle);
      return failEvent("spawn", "desktop_label_failed",
                       L"could not lower the hidden desktop label to match the Blender image",
                       0, kExitDesktopFailed);
    }
  }

  HANDLE job = createJob();
  if (job == nullptr) {
    const DWORD lastError = GetLastError();
    CloseDesktop(desktopHandle);
    return failEvent("spawn", "job_failed", L"could not create the job object", lastError,
                     kExitJobFailed);
  }

  HANDLE outRead = nullptr;
  HANDLE outWrite = nullptr;
  HANDLE errRead = nullptr;
  HANDLE errWrite = nullptr;
  const bool pipesCreated = createPipePair(outRead, outWrite) && createPipePair(errRead, errWrite);
  HANDLE nul = pipesCreated ? createNulInput() : INVALID_HANDLE_VALUE;
  if (!pipesCreated || nul == INVALID_HANDLE_VALUE) {
    const DWORD lastError = GetLastError();
    closeHandle(outRead);
    closeHandle(outWrite);
    closeHandle(errRead);
    closeHandle(errWrite);
    closeHandle(nul);
    CloseHandle(job);
    CloseDesktop(desktopHandle);
    return failEvent("spawn", "pipe_failed", L"could not create the child stdio pipes", lastError,
                     kExitSpawnFailed);
  }

  STARTUPINFOW startup{};
  startup.cb = sizeof(startup);
  startup.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
  startup.wShowWindow = SW_SHOWNORMAL;
  startup.lpDesktop = desktop.data();
  startup.hStdInput = nul;
  startup.hStdOutput = outWrite;
  startup.hStdError = errWrite;

  PROCESS_INFORMATION child{};
  std::wstring commandLine = quoteArg(options.exe);
  for (const std::wstring& argument : options.childArgs) {
    commandLine.push_back(L' ');
    commandLine += quoteArg(argument);
  }
  const wchar_t* currentDirectory = options.cwd.empty() ? nullptr : options.cwd.c_str();
  const BOOL created = CreateProcessW(options.exe.c_str(), commandLine.data(), nullptr, nullptr, TRUE,
                                      CREATE_SUSPENDED, nullptr, currentDirectory, &startup, &child);
  if (!created) {
    const DWORD lastError = GetLastError();
    closeHandle(outRead);
    closeHandle(outWrite);
    closeHandle(errRead);
    closeHandle(errWrite);
    closeHandle(nul);
    CloseHandle(job);
    CloseDesktop(desktopHandle);
    return failEvent("spawn", "spawn_failed", L"could not start Blender", lastError,
                     kExitSpawnFailed);
  }

  closeHandle(outWrite);
  closeHandle(errWrite);
  closeHandle(nul);

  if (!AssignProcessToJobObject(job, child.hProcess)) {
    const DWORD lastError = GetLastError();
    TerminateProcess(child.hProcess, 1);
    CloseHandle(child.hThread);
    CloseHandle(child.hProcess);
    closeHandle(outRead);
    closeHandle(errRead);
    CloseHandle(job);
    CloseDesktop(desktopHandle);
    return failEvent("spawn", "job_assign_failed",
                     L"could not assign Blender to the job object", lastError, kExitSpawnFailed);
  }

  if (ResumeThread(child.hThread) == static_cast<DWORD>(-1)) {
    const DWORD lastError = GetLastError();
    TerminateProcess(child.hProcess, 1);
    CloseHandle(child.hThread);
    CloseHandle(child.hProcess);
    closeHandle(outRead);
    closeHandle(errRead);
    CloseHandle(job);
    CloseDesktop(desktopHandle);
    return failEvent("spawn", "resume_failed", L"could not resume the Blender process", lastError,
                     kExitResumeFailed);
  }

  writeLine(out, std::string("{\"event\":\"ready\",\"helper\":") +
                     std::to_string(GetCurrentProcessId()) + ",\"blender\":" +
                     std::to_string(child.dwProcessId) + ",\"display\":\"" +
                     jsonEscape(utf8(desktop)) + "\",\"exe\":\"" +
                     jsonEscape(utf8(options.exe)) + "\"}");

  HANDLE outThread = startPump(outRead, out);
  HANDLE errThread = startPump(errRead, GetStdHandle(STD_ERROR_HANDLE));

  std::atomic<bool> finished{false};
  HANDLE parent = nullptr;
  if (options.parentPid != 0) {
    parent = OpenProcess(SYNCHRONIZE, FALSE, options.parentPid);
  }
  std::thread watchdog;
  if (parent != nullptr) {
    watchdog = std::thread([&finished, parent, job, process = child.hProcess]() {
      while (!finished.load()) {
        const DWORD wait = WaitForSingleObject(parent, 200);
        if (wait == WAIT_OBJECT_0) {
          logLine("parent process exited; terminating Blender");
          TerminateJobObject(job, 1);
          TerminateProcess(process, 1);
          return;
        }
        if (wait != WAIT_TIMEOUT) {
          return;
        }
      }
    });
  } else if (options.parentPid != 0) {
    logLine("could not open the parent process; the watchdog is disabled");
  }

  DWORD childExitCode = static_cast<DWORD>(kExitInternal);
  const DWORD waitResult = WaitForSingleObject(child.hProcess, INFINITE);
  if (waitResult == WAIT_OBJECT_0) {
    if (!GetExitCodeProcess(child.hProcess, &childExitCode)) {
      childExitCode = static_cast<DWORD>(kExitInternal);
    }
  } else {
    logLine("waiting for Blender failed");
  }

  finished.store(true);
  if (watchdog.joinable()) {
    watchdog.join();
  }
  stopPump(outThread);
  stopPump(errThread);
  closeHandle(parent);
  CloseHandle(child.hThread);
  CloseHandle(child.hProcess);
  CloseHandle(job);
  CloseDesktop(desktopHandle);
  return static_cast<int>(childExitCode);
}
