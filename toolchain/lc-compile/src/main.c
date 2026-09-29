/* Copyright (C) 2003-2015 LiveCode Ltd.
 
 This file is part of LiveCode.
 
 LiveCode is free software; you can redistribute it and/or modify it under
 the terms of the GNU General Public License v3 as published by the Free
 Software Foundation.
 
 LiveCode is distributed in the hope that it will be useful, but WITHOUT ANY
 WARRANTY; without even the implied warranty of MERCHANTABILITY or
 FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
 for more details.
 
 You should have received a copy of the GNU General Public License
 along with LiveCode.  If not see <http://www.gnu.org/licenses/>.  */

#include <stdlib.h>
#include <stdio.h>
#include <string.h>

#if !defined(_WIN32)
#include <pthread.h>
#endif

#include <literal.h>
#include <position.h>
#include <report.h>
#include "outputfile.h"

extern void ROOT(void);
extern void yyExtend(void);
extern void InitializeCustomInvokeLists(void);

static int s_is_bootstrap = 0;

extern enum DependencyModeType DependencyMode;
extern int OutputFileAsC;
extern int OutputFileAsAuxC;
extern int OutputFileAsBytecode;

static int s_force_c_builtins = 0;

int ForceCBindingsAsBuiltins(void)
{
    return s_force_c_builtins;
}

int IsBootstrapCompile(void)
{
    return s_is_bootstrap;
}

int IsNotBytecodeOutput(void)
{
    return OutputFileAsBytecode == 0;
}

int IsDependencyCompile(void)
{
    return DependencyMode != kDependencyModeNone;
}

void bootstrap_main(int argc, char *argv[])
{
    int i;
	
	// If there is no filename, error.
    if (argc == 0)
    {
        fprintf(stderr, "Invalid arguments\n");
        return;
    }
    
    s_is_bootstrap = 1;

    /* Treat all warnings as errors in bootstrap mode */
    s_is_werror_enabled = 1;
    
    for(i = 0; i < argc; i++)
    {
        if (0 == strcmp(argv[i], "-v") || 0 == strcmp(argv[i], "--verbose"))
        {
            ++s_verbose_level;
            continue;
        }
        else if (strcmp(argv[i], "--inputg") == 0 && i + 1 < argc)
           SetTemplateFile(argv[++i]);
        else if (strcmp(argv[i], "--outputg") == 0 && i + 1 < argc)
            SetOutputGrammarFile(argv[++i]);
        else if (strcmp(argv[i], "--outputc") == 0 && i + 1 < argc)
        {
            OutputFileAsC = 1;
            OutputFileAsBytecode = 0;
            SetOutputCodeFile(argv[++i]);
        }
        else if (strcmp(argv[i], "--outputi") == 0 && i + 1 < argc)
            AddImportedModuleDir(argv[++i]);
        else
            AddFile(argv[i]);
    }
    
    if (MoveToNextFile())
    {
        yyExtend();
        InitializeCustomInvokeLists();
        ROOT();
    }
}

/* Print some sort of helpful message if the user doesn't pass sane arguments */
static void
usage(int status)
{
    fprintf(stderr,
"Usage: lc-compile [OPTION ...] --output OUTFILE [--] LCBFILE ... LCBFILE\n"
"       lc-compile [OPTION ...] --outputc OUTFILE [--] LCBFILE ... LCBFILE\n"
"       lc-compile [OPTION ...] --deps DEPTYPE [--] LCBFILE ... LCBFILE\n"
"\n"
"Compile a LiveCode Builder source file.\n"
"\n"
"Options:\n"
"      --modulepath PATH      Search PATH for module interface files.\n"
"      --output OUTFILE       Filename for bytecode output.\n"
"      --outputc OUTFILE      Filename for C source code output.\n"
"      --outputauxc OUTFILE   Filename for C source code output in auxillary mode.\n"
"                             This generates an auxillary set of C source code\n"
"                             modules, which do *not* include the builtin module and\n"
"                             do not produce builtin shims.\n"
"      --deps make            Generate lci file dependencies in make format for\n"
"                             the input source files.\n"
"      --deps order           Generate the order the input source files should be\n"
"                             compiled in.\n"
"      --deps changed-order   Generate the order the input source files should be\n"
"                             compiled in, but only if they need recompiling.\n"
"      --manifest MANIFEST    Filename for generated manifest.\n"
"      --interface INTERFACE  Filename for generated interface.\n"
"      --forcebuiltins        Generate c bindings as builtin shims for auxc output.\n"
"      -Werror                Turn all warnings into errors.\n"
"  -v, --verbose              Output extra debugging information.\n"
"  -h, --help                 Print this message.\n"
"  --                         Treat all remaining arguments as filenames.\n"
"\n"
"More than one `--modulepath' option may be specified.  The PATHs are\n"
"searched in the order they appear.  If the `--interface' option is not\n"
"specified, then an interface file may be generated in the first PATH\n"
"specified.\n"
"\n"
"Report bugs to <http://quality.livecode.com/>\n"
            );
    exit (status);
}

static void full_main(int argc, char *argv[])
{
    /* Process options. */
    int have_input_file = 0;
    int have_output_file = 0;
	int have_interface_file = 0;
	int have_manifest_file = 0;
    int end_of_args = 0;
    int argi;

    /* FIXME maybe we should use getopt? */
    for (argi = 0; argi < argc; ++argi)
    {
        const char *opt = argv[argi];
        const char *optarg = (argi + 1 < argc) && 0 != strncmp(argv[argi+1], "--", 2) ? argv[argi+1] : NULL;
        if (!end_of_args)
        {
            if (0 == strcmp(opt, "--deps"))
            {
                const char *t_option;
                if (optarg)
                    t_option = argv[++argi];
                else
                    t_option = "make";
                
                if (0 == strcmp(t_option, "make"))
                    DependencyMode = kDependencyModeMake;
                else if (0 == strcmp(t_option, "order"))
                    DependencyMode = kDependencyModeOrder;
                else if (0 == strcmp(t_option, "changed-order"))
                    DependencyMode = kDependencyModeChangedOrder;
                else
                {
                    fprintf(stderr, "ERROR: Invalid --deps option '%s'.\n\n",
                            t_option);
                    usage(1);
                }
                continue;
            }
            if (0 == strcmp(opt, "--modulepath") && optarg)
            {
                AddImportedModuleDir(argv[++argi]);
                continue;
            }
            if (0 == strcmp(opt, "--output") && optarg)
            {
                SetOutputBytecodeFile(argv[++argi]);
                OutputFileAsBytecode = 1;
                OutputFileAsC = 0;
                OutputFileAsAuxC = 0;
                have_output_file = 1;
                continue;
            }
            if (0 == strcmp(opt, "--outputc") && optarg)
            {
                SetOutputCodeFile(argv[++argi]);
                OutputFileAsC = 1;
                OutputFileAsAuxC = 0;
                OutputFileAsBytecode = 0;
                have_output_file = 1;
                continue;
            }
            if (0 == strcmp(opt, "--outputauxc") && optarg)
            {
                SetOutputCodeFile(argv[++argi]);
                OutputFileAsC = 1;
                OutputFileAsAuxC = 1;
                OutputFileAsBytecode = 0;
                have_output_file = 1;
                continue;
            }
            if (0 == strcmp(opt, "--manifest") && optarg)
            {
                SetManifestOutputFile(argv[++argi]);
				have_manifest_file = 1;
                continue;
            }
            if (0 == strcmp(opt, "--interface") && optarg)
            {
                SetInterfaceOutputFile(argv[++argi]);
				have_interface_file = 1;
                continue;
            }
            /* FIXME This should be expanded to support "-W error",
             * "--warn error", "--warn=error", etc.  Also options for
             * enabling/disabling/errorifying particular warning
             * types. */
            if (0 == strcmp(opt, "-Werror"))
            {
                s_is_werror_enabled = 1;
                continue;
            }
            if (0 == strcmp(opt, "-v") || 0 == strcmp(opt, "--verbose"))
            {
                ++s_verbose_level;
                continue;
            }
            if (0 == strcmp(opt, "-h") || 0 == strcmp(opt, "--help"))
            {
                usage(0);
                continue;
            }
            if (0 == strcmp(opt, "--forcebuiltins"))
            {
                s_force_c_builtins = 1;
                continue;
            }
            if (0 == strcmp(opt, "--"))
            {
                end_of_args = 1;
                continue;
            }

            /* Detect unrecognized arguments */
            if ('-' == opt[0])
            {
                fprintf(stderr, "ERROR: Invalid option '%s'.\n\n",
                        argv[argi]);
                usage(1);
            }
        }

        /* Accept any number of input files */
        AddFile(opt);
        have_input_file = 1;
    }

	// If there is no filename, error.
    if (!have_input_file)
    {
        fprintf(stderr, "ERROR: You must specify an input filename.\n\n");
        usage(1);
    }

    if (MoveToNextFile())
    {
        yyExtend();
        InitializeCustomInvokeLists();
        ROOT();
    }
}

extern int yydebug;
extern void InitializeFoundation(void);

static int lc_compile_main(int argc, char *argv[]);

#if !defined(_WIN32)
/* The parser recurses deeply. Windows links lc-compile with a 64 MB stack
   (StackReserveSize in lc-compile-bootstrap.gyp); elsewhere the main thread
   gets the default (usually 8 MB), and the compiler crashed on Linux arm64,
   whose stack frames are larger. So run the compiler on a thread with a
   64 MB stack there too. */
struct lc_compile_args
{
    int argc;
    char **argv;
    int result;
};

static void *lc_compile_thread(void *p_context)
{
    struct lc_compile_args *t_args;
    t_args = (struct lc_compile_args *)p_context;
    t_args -> result = lc_compile_main(t_args -> argc, t_args -> argv);
    return NULL;
}

int main(int argc, char *argv[])
{
    struct lc_compile_args t_args;
    pthread_attr_t t_attr;
    pthread_t t_thread;
    int t_started;

    t_args . argc = argc;
    t_args . argv = argv;
    t_args . result = 1;

    t_started = 0;
    if (pthread_attr_init(&t_attr) == 0)
    {
        if (pthread_attr_setstacksize(&t_attr, 64 * 1024 * 1024) == 0 &&
            pthread_create(&t_thread, &t_attr, lc_compile_thread, &t_args) == 0)
            t_started = 1;
        pthread_attr_destroy(&t_attr);
    }

    if (!t_started)
        return lc_compile_main(argc, argv);

    pthread_join(t_thread, NULL);
    return t_args . result;
}
#else
int main(int argc, char *argv[])
{
    return lc_compile_main(argc, argv);
}
#endif

static int lc_compile_main(int argc, char *argv[])
{
    //extern int yydebug;
	int t_return_code;
	
	// Skip command arg.
    argc -= 1;
    argv += 1;
    
    // Check for debug mode.
    if (argc > 1 && strcmp(argv[0], "--debug") == 0)
    {
        argc -= 1;
        argv += 1;
        
#ifdef YYDEBUG
        yydebug = 1;
#endif
    }
    
    // Initialize libfoundation
    InitializeFoundation();
    
    InitializeFiles();
    InitializePosition();
    InitializeLiterals();
    InitializeReports();
    InitializeScopes();
    
    // Check for bootstrap mode.
    if (argc > 1 && strcmp(argv[0], "--bootstrap") == 0)
        bootstrap_main(argc - 1, argv + 1);
    else
        full_main(argc, argv);

    if (ErrorsDidOccur())
        t_return_code = 1;
    else
        t_return_code = 0;
    
    FinalizeScopes();
    FinalizeReports();
    FinalizeLiterals();
    FinalizePosition();
    FinalizeFiles();
    
    return t_return_code;
    
    /*extern FILE *yyin;
    yyin = fopen(argv[0], "r");
    if (yyin == NULL)
    {
        fprintf(stderr, "Could not open file '%s'\n", argv[0]);
        return 1;
    }
    
    Run();
    
    return 0;*/
}
