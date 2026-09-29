{
	'includes':
	[
		'../../../common.gypi',
	],
	
	'targets':
	[
		{
			'target_name': 'reflex',
			'type': 'executable',
			
			'toolsets': ['host','target'],
			
			'product_name': 'reflex-<(_toolset)',
		
			'variables':
			{
				'silence_warnings': 1,
			},

			# OXT-Beyond: gentle and reflex are pre-ANSI C (implicit int,
			# functions used before they are declared). Current clang rejects
			# that from C99 on even with warnings silenced, so compile them as
			# gnu89, where it is valid.
			'xcode_settings':
			{
				'GCC_C_LANGUAGE_STANDARD': 'gnu89',
				'OTHER_CFLAGS':
				[
					'-Wno-error=int-conversion',
					'-Wno-error=incompatible-pointer-types',
					'-Wno-error=incompatible-function-pointer-types',
				],
			},
	
			'direct_dependent_settings':
			{
				'variables':
				{
					'reflex_exe_file': '<(PRODUCT_DIR)/<(_product_name)<(EXECUTABLE_SUFFIX)',
				},
			},
			
			'sources':
			[
				'reflex.c',
			],
			
			'msvs_settings':
			{
				'VCLinkerTool':
				{
					'SubSystem': '1',	# /SUBSYSTEM:CONSOLE
				},
			},
		},
	],
}

