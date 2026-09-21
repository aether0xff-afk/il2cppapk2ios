#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>
#import <dlfcn.h>

typedef void (*il2cpp_init_fn)(const char *);
typedef void (*il2cpp_shutdown_fn)(void);

static UILabel *gStatusLabel;

static void a2i_status(NSString *message) {
    NSLog(@"[a2i] %@", message);
    dispatch_async(dispatch_get_main_queue(), ^{
        gStatusLabel.text = [NSString stringWithFormat:@"%@\n\n%@", gStatusLabel.text ?: @"", message];
    });
}

static void a2i_log_dlerror(NSString *stage) {
    const char *err = dlerror();
    NSString *message = [NSString stringWithFormat:@"%@: %s", stage, err ? err : "(no dlerror)"];
    a2i_status(message);
}

static void a2i_probe(void) {
    @autoreleasepool {
        a2i_status(@"iOS host started");
        NSBundle *bundle = NSBundle.mainBundle;
        NSString *frameworks = bundle.privateFrameworksPath;
        NSString *shimPath = [frameworks stringByAppendingPathComponent:@"libbionic_shim.dylib"];
        NSString *il2cppPath = [frameworks stringByAppendingPathComponent:@"libil2cpp_ported.dylib"];
        if (!shimPath || !il2cppPath) {
            NSLog(@"[a2i] missing embedded dylib(s): shim=%@ il2cpp=%@", shimPath, il2cppPath);
            return;
        }
        dlerror();
        void *shim = dlopen(shimPath.fileSystemRepresentation, RTLD_NOW | RTLD_GLOBAL);
        if (!shim) { a2i_log_dlerror(@"dlopen shim failed"); return; }
        a2i_status(@"shim loaded");
        dlerror();
        void *il2cpp = dlopen(il2cppPath.fileSystemRepresentation, RTLD_NOW | RTLD_GLOBAL);
        if (!il2cpp) { a2i_log_dlerror(@"dlopen translated libil2cpp failed"); return; }
        a2i_status(@"translated libil2cpp loaded");
        dlerror();
        il2cpp_init_fn il2cpp_init = (il2cpp_init_fn)dlsym(il2cpp, "il2cpp_init");
        if (!il2cpp_init) { a2i_log_dlerror(@"dlsym il2cpp_init failed"); return; }
        a2i_status([NSString stringWithFormat:@"il2cpp_init=%p", il2cpp_init]);
        NSString *metadata = [bundle pathForResource:@"global-metadata" ofType:@"dat"];
        if (metadata) {
            NSString *metadataDir = metadata.stringByDeletingLastPathComponent;
            setenv("A2I_METADATA_DIR", metadataDir.fileSystemRepresentation, 1);
            a2i_status([NSString stringWithFormat:@"metadata present: %@", metadata.lastPathComponent]);
        } else {
            a2i_status(@"global-metadata.dat missing");
        }
        a2i_status(@"calling il2cpp_init");
        il2cpp_init("a2i-ios-host");
        a2i_status(@"il2cpp_init returned");
        il2cpp_shutdown_fn shutdown = (il2cpp_shutdown_fn)dlsym(il2cpp, "il2cpp_shutdown");
        if (shutdown) { NSLog(@"[a2i] calling il2cpp_shutdown"); shutdown(); }
        dlclose(il2cpp);
        dlclose(shim);
        a2i_status(@"probe complete");
    }
}

@interface A2IAppDelegate : UIResponder <UIApplicationDelegate>
@property (strong, nonatomic) UIWindow *window;
@end

@implementation A2IAppDelegate
- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)launchOptions {
    self.window = [[UIWindow alloc] initWithFrame:UIScreen.mainScreen.bounds];
    UIViewController *vc = [UIViewController new];
    vc.view.backgroundColor = UIColor.whiteColor;
    UILabel *label = [[UILabel alloc] initWithFrame:CGRectZero];
    gStatusLabel = label;
    label.font = [UIFont monospacedSystemFontOfSize:13 weight:UIFontWeightRegular];
    label.text = @"A2I IL2CPP probe running…";
    label.textAlignment = NSTextAlignmentCenter;
    label.numberOfLines = 0;
    label.translatesAutoresizingMaskIntoConstraints = NO;
    [vc.view addSubview:label];
    [NSLayoutConstraint activateConstraints:@[
        [label.centerXAnchor constraintEqualToAnchor:vc.view.centerXAnchor],
        [label.centerYAnchor constraintEqualToAnchor:vc.view.centerYAnchor],
        [label.leadingAnchor constraintGreaterThanOrEqualToAnchor:vc.view.leadingAnchor constant:20],
        [label.trailingAnchor constraintLessThanOrEqualToAnchor:vc.view.trailingAnchor constant:-20],
    ]];
    self.window.rootViewController = vc;
    [self.window makeKeyAndVisible];
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{ a2i_probe(); });
    return YES;
}
@end

int main(int argc, char **argv) {
    @autoreleasepool {
        return UIApplicationMain(argc, argv, nil, NSStringFromClass(A2IAppDelegate.class));
    }
}
