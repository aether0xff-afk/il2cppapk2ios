#import <Foundation/Foundation.h>
#import <dlfcn.h>

typedef void (*il2cpp_init_fn)(const char *);
typedef void (*il2cpp_shutdown_fn)(void);
typedef const char *(*il2cpp_get_corlib_fn)(void);

static void a2i_log_dlerror(NSString *stage) {
    const char *err = dlerror();
    NSLog(@"[a2i] %@: %s", stage, err ? err : "(no dlerror)");
}

int main(int argc, char **argv) {
    @autoreleasepool {
        NSLog(@"[a2i] iOS host started");

        NSBundle *bundle = NSBundle.mainBundle;
        NSString *shimPath = [bundle pathForResource:@"libbionic_shim" ofType:@"dylib"];
        NSString *il2cppPath = [bundle pathForResource:@"libil2cpp_ported" ofType:@"dylib"];

        if (!shimPath || !il2cppPath) {
            NSLog(@"[a2i] missing embedded dylib(s): shim=%@ il2cpp=%@", shimPath, il2cppPath);
            return 10;
        }

        dlerror();
        void *shim = dlopen(shimPath.fileSystemRepresentation, RTLD_NOW | RTLD_GLOBAL);
        if (!shim) {
            a2i_log_dlerror(@"dlopen shim failed");
            return 11;
        }
        NSLog(@"[a2i] shim loaded");

        dlerror();
        void *il2cpp = dlopen(il2cppPath.fileSystemRepresentation, RTLD_NOW | RTLD_GLOBAL);
        if (!il2cpp) {
            a2i_log_dlerror(@"dlopen translated libil2cpp failed");
            return 12;
        }
        NSLog(@"[a2i] translated libil2cpp loaded");

        dlerror();
        il2cpp_init_fn il2cpp_init = (il2cpp_init_fn)dlsym(il2cpp, "il2cpp_init");
        if (!il2cpp_init) {
            a2i_log_dlerror(@"dlsym il2cpp_init failed");
            return 13;
        }
        NSLog(@"[a2i] il2cpp_init=%p", il2cpp_init);

        NSString *metadata = [bundle pathForResource:@"global-metadata" ofType:@"dat"];
        if (metadata) {
            NSString *metadataDir = metadata.stringByDeletingLastPathComponent;
            setenv("A2I_METADATA_DIR", metadataDir.fileSystemRepresentation, 1);
            NSLog(@"[a2i] metadata present at %@", metadata);
        } else {
            NSLog(@"[a2i] global-metadata.dat is not embedded; init may fail");
        }

        NSLog(@"[a2i] calling il2cpp_init");
        il2cpp_init("a2i-ios-host");
        NSLog(@"[a2i] il2cpp_init returned");

        il2cpp_shutdown_fn shutdown =
            (il2cpp_shutdown_fn)dlsym(il2cpp, "il2cpp_shutdown");
        if (shutdown) {
            NSLog(@"[a2i] calling il2cpp_shutdown");
            shutdown();
        }

        dlclose(il2cpp);
        dlclose(shim);
        NSLog(@"[a2i] probe complete");
        return 0;
    }
}
