/*
 * wine_locale.c — Bionic setlocale() shim for the Wine process (loaded via LD_PRELOAD).
 *
 * Problem it fixes
 * ----------------
 * Wine's ntdll derives the *Windows system locale*, and therefore the ANSI code page that
 * GetACP()/WideCharToMultiByte(CP_ACP) use, from the Unix locale returned by
 * setlocale(LC_CTYPE, NULL):
 *
 *   dlls/ntdll/unix/env.c: init_locale()
 *       setlocale( LC_ALL, "" );
 *       unix_to_win_locale( setlocale( LC_CTYPE, NULL ), system_locale );
 *
 * and unix_to_win_locale() only consults $LC_ALL when that string is empty, "C" or "POSIX":
 *
 *   if (!unix_name || !unix_name[0] || !strcmp( unix_name, "C" ))
 *   {
 *       unix_name = getenv( "LC_ALL" );
 *       ...
 *   }
 *
 * Android's Bionic libc has no real locales: setlocale(LC_ALL, "") returns "C.UTF-8" for
 * every query, and setlocale(LC_*, "zh_CN.UTF-8") fails (returns NULL).  So on this device
 * Wine sees "C.UTF-8" (not "C"), never falls back to $LC_ALL, keeps an en-US system locale
 * and an ANSI code page of 1252.  Result: the container's LC_ALL=zh_CN.UTF-8 is ignored and
 * every Chinese character that travels through an ANSI API — game UI text, a GBK-encoded
 * .txt opened in notepad, ANSI file paths passed to ShellExecuteExA — becomes "?" or
 * mojibake, even though CJK fonts are installed in the prefix.
 *
 * What this shim does
 * -------------------
 * It reports the plain classic "C" locale from setlocale(), which is exactly the condition
 * Wine's env.c looks for, so Wine reads $LC_ALL itself.  With LC_ALL=zh_CN.UTF-8 that maps
 * to the Windows locale "zh-CN" (LCID 0x0804) and the ANSI code page 936; Wine ships the
 * c_936.nls table in every layer, so GBK conversion then round-trips and Chinese renders.
 *
 * Actual set requests are still forwarded to Bionic so the process keeps its real behaviour;
 * only the queried name is pinned to "C".  One interposed symbol, no Android/JNI
 * dependencies — safe to preload into the box64/wine process tree.
 */

#define _GNU_SOURCE
#include <dlfcn.h>
#include <locale.h>

typedef char *(*setlocale_fn)(int, const char *);

char *setlocale(int category, const char *locale) {
    static setlocale_fn real_setlocale;
    if (real_setlocale == NULL) {
        real_setlocale = (setlocale_fn)dlsym(RTLD_NEXT, "setlocale");
    }

    /* Genuine set requests keep Bionic's own (C.UTF-8) state and return value. */
    if (locale != NULL) {
        return real_setlocale != NULL ? real_setlocale(category, locale) : (char *)"C";
    }

    /* Queries report "C" so Wine's ntdll (env.c) takes its $LC_ALL fallback →
     * zh_CN.UTF-8 → "zh-CN" → ACP 936. */
    return (char *)"C";
}
