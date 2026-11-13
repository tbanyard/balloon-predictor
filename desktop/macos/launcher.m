#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>
#import <QuartzCore/QuartzCore.h>
#include <math.h>

#if defined(__arm64__)
static NSString *const nativeArch = @"arm64";
#elif defined(__x86_64__)
static NSString *const nativeArch = @"x86_64";
#else
#error Unsupported Mac architecture
#endif

@interface BalloonApp : NSObject <NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate>
@property(nonatomic, strong) NSWindow *window;
@property(nonatomic, strong) NSWindow *splash;
@property(nonatomic) NSTimeInterval splashStart;
@property(nonatomic, strong) WKWebView *web;
@property(nonatomic, strong) NSTask *backend;
@property(nonatomic, strong) NSFileHandle *shutdownPipe;
@property(nonatomic, strong) NSURL *dataDir;
@property(nonatomic, strong) NSURL *sessionDir;
@property(nonatomic, strong) NSURL *readyFile;
@property(nonatomic, strong) NSURL *origin;
@property(nonatomic, strong) NSURL *testRoot;
@property(nonatomic, strong) NSTimer *readyTimer;
@property(nonatomic, strong) NSTimer *testTimer;
@property(nonatomic, strong) NSDictionary *engineReport;
@property(nonatomic, strong) NSMapTable<WKDownload *, NSDictionary *> *downloads;
@property(nonatomic) BOOL quitting;
@property(nonatomic) BOOL testPageLoaded;
@property(nonatomic) BOOL testProbePending;
@property(nonatomic) BOOL testRequestedDownload;
@property(nonatomic, copy) NSString *testStage;
@end

@implementation BalloonApp

- (void)showAlert:(NSString *)title detail:(NSString *)detail {
    NSAlert *alert = [NSAlert new];
    alert.messageText = title;
    alert.informativeText = detail ?: @"";
    [alert runModal];
}

- (void)fail:(NSString *)message {
    if (self.quitting) return;
    self.quitting = YES;
    NSLog(@"BalloonPredictor: %@", message);
    if (!self.testRoot) {
        NSString *log = [self.dataDir URLByAppendingPathComponent:@"BalloonPredictor.log"].path;
        NSString *detail = log ? [NSString stringWithFormat:@"%@\n\nLog: %@", message, log] : message;
        [self showAlert:@"BalloonPredictor could not start" detail:detail];
    }
    [NSApp terminate:nil];
}

- (void)makeMenu {
    NSMenu *bar = [NSMenu new];
    NSMenuItem *application = [NSMenuItem new];
    [bar addItem:application];
    NSMenu *appMenu = [[NSMenu alloc] initWithTitle:@"BalloonPredictor"];
    NSMenuItem *connect = [appMenu addItemWithTitle:@"Connection…" action:@selector(showConnection:) keyEquivalent:@","];
    connect.target = self;
    [appMenu addItemWithTitle:@"Quit BalloonPredictor" action:@selector(terminate:) keyEquivalent:@"q"];
    application.submenu = appMenu;

    NSMenuItem *edit = [NSMenuItem new];
    [bar addItem:edit];
    NSMenu *editMenu = [[NSMenu alloc] initWithTitle:@"Edit"];
    for (NSArray *item in @[@[@"Undo", @"undo:", @"z"], @[@"Cut", @"cut:", @"x"],
                             @[@"Copy", @"copy:", @"c"], @[@"Paste", @"paste:", @"v"],
                             @[@"Select All", @"selectAll:", @"a"]]) {
        [editMenu addItemWithTitle:item[0] action:NSSelectorFromString(item[1]) keyEquivalent:item[2]];
    }
    edit.submenu = editMenu;
    NSApp.mainMenu = bar;
}

- (void)showConnection:(id)sender {
    if (self.web && self.origin) [self.web loadRequest:[NSURLRequest requestWithURL:[NSURL URLWithString:[self.origin.absoluteString stringByAppendingString:@"?splash=done"]]]];
}

- (void)showSplash {
    self.splash = [[NSWindow alloc] initWithContentRect:NSMakeRect(0,0,324,268)
                      styleMask:NSWindowStyleMaskBorderless backing:NSBackingStoreBuffered defer:NO];
    self.splash.opaque = NO;
    self.splash.backgroundColor = NSColor.clearColor;
    self.splash.hasShadow = YES;
    self.splash.releasedWhenClosed = NO;
    NSView *card = [[NSView alloc] initWithFrame:NSMakeRect(10,10,304,248)];
    card.wantsLayer = YES;
    card.layer.cornerRadius = 24;
    card.layer.backgroundColor = [NSColor colorWithRed:1 green:.968 blue:.937 alpha:1].CGColor;
    NSView *root = [[NSView alloc] initWithFrame:NSMakeRect(0,0,324,268)];
    [root addSubview:card];
    self.splash.contentView = root;
    NSImageView *logo = [[NSImageView alloc] initWithFrame:NSMakeRect(91,97,122,122)];
    NSURL *icon = [NSBundle.mainBundle URLForResource:@"BalloonPredictor" withExtension:@"icns"];
    logo.image = [[NSImage alloc] initWithContentsOfURL:icon];
    logo.imageScaling = NSImageScaleProportionallyUpOrDown;
    logo.wantsLayer = YES;
    [card addSubview:logo];
    if (!NSWorkspace.sharedWorkspace.accessibilityDisplayShouldReduceMotion) {
        CABasicAnimation *rise = [CABasicAnimation animationWithKeyPath:@"transform.translation.y"];
        rise.fromValue = @(-4); rise.toValue = @6; rise.duration = .78;
        rise.autoreverses = YES; rise.repeatCount = HUGE_VALF;
        rise.timingFunction = [CAMediaTimingFunction functionWithName:kCAMediaTimingFunctionEaseInEaseOut];
        [logo.layer addAnimation:rise forKey:@"balloon-rise"];
    }
    NSTextField *label = [NSTextField labelWithString:@"BalloonPredictor"];
    label.frame = NSMakeRect(0,60,304,30);
    label.font = [NSFont boldSystemFontOfSize:23];
    label.textColor = [NSColor colorWithRed:.125 green:.23 blue:.195 alpha:1];
    label.alignment = NSTextAlignmentCenter;
    [card addSubview:label];
    NSTextField *caption = [NSTextField labelWithString:@"ATMOSPHERIC FLIGHT LAB"];
    caption.frame = NSMakeRect(0,34,304,17);
    caption.font = [NSFont systemFontOfSize:11 weight:NSFontWeightSemibold];
    caption.textColor = [NSColor colorWithRed:.54 green:.37 blue:.30 alpha:1];
    caption.alignment = NSTextAlignmentCenter;
    [card addSubview:caption];
    [self.splash center];
    [self.splash orderFront:nil];
    self.splashStart = NSProcessInfo.processInfo.systemUptime;
}

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    [self makeMenu];
    self.downloads = [NSMapTable strongToStrongObjectsMapTable];
    NSFileManager *files = [NSFileManager defaultManager];
    NSError *error = nil;
    NSURL *support = self.testRoot ?: [files URLForDirectory:NSApplicationSupportDirectory
                                                  inDomain:NSUserDomainMask appropriateForURL:nil
                                                     create:YES error:&error];
    if (!support) {
        [self fail:error.localizedDescription ?: @"The user data folder is unavailable."];
        return;
    }
    self.dataDir = self.testRoot ?: [support URLByAppendingPathComponent:@"BalloonPredictor" isDirectory:YES];
    if (![files createDirectoryAtURL:self.dataDir withIntermediateDirectories:YES attributes:nil error:&error]) {
        [self fail:error.localizedDescription];
        return;
    }
    self.sessionDir = [NSURL fileURLWithPath:[NSTemporaryDirectory()
        stringByAppendingPathComponent:[@"balloon-predictor-" stringByAppendingString:NSUUID.UUID.UUIDString]]
                                isDirectory:YES];
    if (![files createDirectoryAtURL:self.sessionDir withIntermediateDirectories:NO
                          attributes:@{NSFilePosixPermissions: @0700} error:&error]) {
        [self fail:error.localizedDescription];
        return;
    }
    self.readyFile = [self.sessionDir URLByAppendingPathComponent:@"ready.json"];
    NSURL *logURL = [self.dataDir URLByAppendingPathComponent:@"BalloonPredictor.log"];
    if (![files fileExistsAtPath:logURL.path]) [files createFileAtPath:logURL.path contents:nil attributes:nil];
    NSFileHandle *log = [NSFileHandle fileHandleForWritingToURL:logURL error:&error];
    if (!log) {
        [self fail:error.localizedDescription ?: @"The app log is unavailable."];
        return;
    }
    [log seekToEndOfFile];

    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 1280, 850)
                      styleMask:(NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                                 NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable)
                        backing:NSBackingStoreBuffered defer:NO];
    self.window.title = @"BalloonPredictor";
    self.window.minSize = NSMakeSize(760, 570);
    self.window.releasedWhenClosed = NO;
    [self.window center];
    [self showSplash];
    [NSApp activate];

    NSURL *resources = NSBundle.mainBundle.resourceURL;
    NSURL *source = [resources URLByAppendingPathComponent:@"app" isDirectory:YES];
    NSString *engine = [NSString stringWithFormat:@"runtime/%@/bin/python3.13", nativeArch];
    self.backend = [NSTask new];
    self.backend.executableURL = [resources URLByAppendingPathComponent:engine];
    self.backend.currentDirectoryURL = self.dataDir;
    NSMutableArray *arguments = [NSMutableArray arrayWithArray:@[
        @"-I", @"-B", @"-u", [source URLByAppendingPathComponent:@"desktop/host.py"].path,
        @"--data-dir", self.dataDir.path, @"--ready-file", self.readyFile.path
    ]];
    if (self.testRoot) [arguments addObject:@"--smoke-test"];
    self.backend.arguments = arguments;
    NSPipe *input = [NSPipe pipe];
    self.shutdownPipe = input.fileHandleForWriting;
    self.backend.standardInput = input;
    self.backend.standardOutput = log;
    self.backend.standardError = log;
    __weak BalloonApp *weakSelf = self;
    self.backend.terminationHandler = ^(NSTask *task) {
        dispatch_async(dispatch_get_main_queue(), ^{
            [weakSelf fail:[NSString stringWithFormat:@"The local engine exited (status %d).", task.terminationStatus]];
        });
    };
    if (![self.backend launchAndReturnError:&error]) {
        [self fail:error.localizedDescription];
        return;
    }
    [log closeFile];

    NSTimeInterval deadline = NSProcessInfo.processInfo.systemUptime + 90;
    self.readyTimer = [NSTimer scheduledTimerWithTimeInterval:0.15 repeats:YES block:^(NSTimer *timer) {
        [weakSelf checkReady:timer deadline:deadline];
    }];
}

- (void)checkReady:(NSTimer *)timer deadline:(NSTimeInterval)deadline {
    NSData *data = [NSData dataWithContentsOfURL:self.readyFile];
    if (!data) {
        if (NSProcessInfo.processInfo.systemUptime > deadline) [self fail:@"The local engine did not finish starting."];
        return;
    }
    if (NSProcessInfo.processInfo.systemUptime - self.splashStart < 2.0) return;
    [timer invalidate];
    id parsed = [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
    if (![parsed isKindOfClass:NSDictionary.class]) {
        [self fail:@"The local engine returned an invalid readiness signal."];
        return;
    }
    self.engineReport = parsed;
    NSString *url = self.engineReport[@"url"];
    NSURL *origin = [url isKindOfClass:NSString.class] ? [NSURL URLWithString:url] : nil;
    if (![origin.scheme isEqual:@"http"] || ![origin.host isEqual:@"127.0.0.1"] ||
        origin.port.integerValue < 1 || origin.port.integerValue > 65535 ||
        origin.user || origin.password || origin.query || origin.fragment ||
        ![self.engineReport[@"arch"] isEqual:nativeArch]) {
        [self fail:@"The local engine did not start with the expected address and processor architecture."];
        return;
    }
    self.origin = origin;
    WKWebViewConfiguration *config = [WKWebViewConfiguration new];
    if (self.testRoot) config.websiteDataStore = WKWebsiteDataStore.nonPersistentDataStore;
    self.web = [[WKWebView alloc] initWithFrame:self.window.contentView.bounds configuration:config];
    self.web.navigationDelegate = self;
    self.web.UIDelegate = self;
    self.window.contentView = self.web;
    [self.window makeKeyAndOrderFront:nil];
    [self.splash orderOut:nil];
    self.splash = nil;
    [self showConnection:nil];
    if (self.testRoot) {
        __weak BalloonApp *weakSelf = self;
        self.testStage = @"loading the connection screen";
        self.testTimer = [NSTimer scheduledTimerWithTimeInterval:0.25 repeats:YES block:^(NSTimer *poll) {
            [weakSelf checkTestWindow];
        }];
        [NSTimer scheduledTimerWithTimeInterval:60 repeats:NO block:^(NSTimer *timeout) {
            [weakSelf fail:[NSString stringWithFormat:@"The WebKit test timed out while %@.", weakSelf.testStage]];
        }];
    }
}

- (void)checkTestWindow {
    if (!self.testPageLoaded || self.testProbePending || self.testRequestedDownload) return;
    NSString *script = @"(() => { const form = document.querySelector('#connection');"
        @"if (form) {"
        @"  const status = document.querySelector('#status.error');"
        @"  if (status?.textContent) return 'error: ' + status.textContent;"
        @"  if (!window.__bpLocal && form.dataset.ready === 'true' && !document.querySelector('#connect').disabled) {"
        @"    const mode = document.querySelector('#mode'); mode.value = 'local';"
        @"    mode.dispatchEvent(new Event('change')); form.requestSubmit(); window.__bpLocal = true;"
        @"  }"
        @"  return window.__bpLocal ? 'opening local mode' : 'waiting for the connection screen to be ready';"
        @"}"
        @"const button = document.querySelector('#predict-button');"
        @"if (!window.__bpTestStarted) {"
        @"if (!window.L || document.querySelector('#dataset')?.value !== 'demo' || button?.disabled !== false)"
        @"  return 'waiting for the flight controls';"
        @"document.querySelector('#flight_mode').value = 'ascent';"
        @"document.querySelector('#burst_altitude').value = '1000';"
        @"document.querySelector('#ascent_rate').value = '5'; button.click(); window.__bpTestStarted = true;"
        @"}"
        @"const status = document.querySelector('#flight-status')?.textContent || '';"
        @"return status.startsWith('Flight complete') ? 'ready' : 'calculating the Demo flight'; })()";
    self.testProbePending = YES;
    [self.web evaluateJavaScript:script completionHandler:^(id value, NSError *error) {
        self.testProbePending = NO;
        if (error || self.quitting || self.testRequestedDownload || ![value isKindOfClass:NSString.class]) return;
        if ([value hasPrefix:@"error: "]) {
            [self fail:[NSString stringWithFormat:@"The connection screen reported an error: %@", [value substringFromIndex:7]]];
            return;
        }
        if (![value isEqualToString:@"ready"]) {
            if (![value isEqualToString:self.testStage]) {
                self.testStage = value;
                NSLog(@"BalloonPredictor WebKit test: %@.", value);
            }
            return;
        }
        NSString *path = self.engineReport[@"download"];
        if (![path isKindOfClass:NSString.class] || ![path hasPrefix:@"/api/exports/"] || ![path hasSuffix:@"/download"]) {
            [self fail:@"The runtime test did not provide a NetCDF export."];
            return;
        }
        NSURL *target = [self selectedOrigin];
        NSURL *url = target ? [NSURL URLWithString:path relativeToURL:target].absoluteURL : nil;
        if (![self isLocal:url]) {
            [self fail:@"The runtime test did not provide a valid local NetCDF download URL."];
            return;
        }
        self.testRequestedDownload = YES;
        self.testStage = @"saving the model box NetCDF";
        NSLog(@"BalloonPredictor WebKit test: %@.", self.testStage);
        [self.testTimer invalidate];
        [self.web loadRequest:[NSURLRequest requestWithURL:url]];
    }];
}

- (NSURL *)selectedOrigin {
    NSData *data = [NSData dataWithContentsOfURL:[self.sessionDir URLByAppendingPathComponent:@"target.json"]];
    if (!data) return nil;
    id json = [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
    if (![json isKindOfClass:NSDictionary.class] || ![json[@"url"] isKindOfClass:NSString.class]) return nil;
    return [NSURL URLWithString:json[@"url"]];
}

- (BOOL)isLocal:(NSURL *)url {
    if (!url || !self.origin) return NO;
    NSURL *target = [self selectedOrigin];
    if ([url.scheme isEqual:@"blob"]) {
        return target && [url.absoluteString hasPrefix:[NSString stringWithFormat:@"blob:%@/", target.absoluteString]];
    }
    if (url.user || url.password) return NO;
    NSInteger port = url.port ? url.port.integerValue : ([url.scheme isEqual:@"https"] ? 443 : 80);
    NSInteger targetPort = target.port ? target.port.integerValue : ([target.scheme isEqual:@"https"] ? 443 : 80);
    return ([url.scheme isEqual:@"http"] && [url.host isEqual:@"127.0.0.1"] && [url.port isEqual:self.origin.port]) ||
           (target && [url.scheme isEqual:target.scheme] && [url.host isEqual:target.host] && port == targetPort);
}

- (void)openLink:(WKNavigationAction *)action {
    NSURL *url = action.request.URL;
    if (action.navigationType == WKNavigationTypeLinkActivated &&
        ([url.scheme isEqual:@"https"] || [self isLocal:url])) {
        [NSWorkspace.sharedWorkspace openURL:url];
    }
}

- (void)webView:(WKWebView *)webView decidePolicyForNavigationAction:(WKNavigationAction *)action
    decisionHandler:(void (^)(WKNavigationActionPolicy))decide {
    if ([self isLocal:action.request.URL] && action.shouldPerformDownload) {
        decide(WKNavigationActionPolicyDownload);
    } else if ([self isLocal:action.request.URL] &&
               (action.targetFrame || action.navigationType != WKNavigationTypeLinkActivated)) {
        decide(WKNavigationActionPolicyAllow);
    } else {
        [self openLink:action];
        decide(WKNavigationActionPolicyCancel);
    }
}

- (WKWebView *)webView:(WKWebView *)webView createWebViewWithConfiguration:(WKWebViewConfiguration *)configuration
    forNavigationAction:(WKNavigationAction *)action windowFeatures:(WKWindowFeatures *)features {
    [self openLink:action];
    return nil;
}

- (void)webView:(WKWebView *)webView decidePolicyForNavigationResponse:(WKNavigationResponse *)response
    decisionHandler:(void (^)(WKNavigationResponsePolicy))decide {
    if (![self isLocal:response.response.URL]) {
        decide(WKNavigationResponsePolicyCancel);
        return;
    }
    NSHTTPURLResponse *http = (NSHTTPURLResponse *)response.response;
    BOOL attachment = [http isKindOfClass:NSHTTPURLResponse.class] &&
        [[[http valueForHTTPHeaderField:@"Content-Disposition"] lowercaseString] hasPrefix:@"attachment"];
    decide((attachment || !response.canShowMIMEType) ? WKNavigationResponsePolicyDownload : WKNavigationResponsePolicyAllow);
}

- (void)webView:(WKWebView *)webView navigationAction:(WKNavigationAction *)action didBecomeDownload:(WKDownload *)download {
    download.delegate = self;
}

- (void)webView:(WKWebView *)webView navigationResponse:(WKNavigationResponse *)response didBecomeDownload:(WKDownload *)download {
    download.delegate = self;
}

- (void)webView:(WKWebView *)webView didFinishNavigation:(WKNavigation *)navigation {
    if (!self.testRoot) return;
    self.testPageLoaded = YES;
    NSLog(@"BalloonPredictor WebKit test: %@ page loaded.",
          [webView.URL.port isEqual:self.origin.port] ? @"connection" : @"flight");
}

- (void)webView:(WKWebView *)webView didFailProvisionalNavigation:(WKNavigation *)navigation withError:(NSError *)error {
    if (error.code == NSURLErrorCancelled ||
        ([error.domain isEqual:@"WebKitErrorDomain"] && error.code == 102)) return;
    [self fail:error.localizedDescription];
}

- (void)webView:(WKWebView *)webView runJavaScriptConfirmPanelWithMessage:(NSString *)message
    initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(BOOL))complete {
    NSAlert *alert = [NSAlert new];
    alert.messageText = message;
    [alert addButtonWithTitle:@"OK"];
    [alert addButtonWithTitle:@"Cancel"];
    [alert beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse response) {
        complete(response == NSAlertFirstButtonReturn);
    }];
}

- (void)webView:(WKWebView *)webView runOpenPanelWithParameters:(WKOpenPanelParameters *)parameters
    initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(NSArray<NSURL *> *))complete {
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.canChooseFiles = YES;
    panel.canChooseDirectories = parameters.allowsDirectories;
    panel.allowsMultipleSelection = parameters.allowsMultipleSelection;
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse response) {
        complete(response == NSModalResponseOK ? panel.URLs : nil);
    }];
}

- (void)download:(WKDownload *)download decideDestinationUsingResponse:(NSURLResponse *)response
    suggestedFilename:(NSString *)filename completionHandler:(void (^)(NSURL *))complete {
    void (^start)(NSURL *) = ^(NSURL *destination) {
        if (!destination) {
            complete(nil);
            return;
        }
        NSURL *temp = [destination.URLByDeletingLastPathComponent
            URLByAppendingPathComponent:[@".balloon-download-" stringByAppendingString:NSUUID.UUID.UUIDString]];
        BOOL replace = [[NSFileManager defaultManager] fileExistsAtPath:destination.path];
        [self.downloads setObject:@{@"destination": destination, @"temp": temp, @"replace": @(replace)}
                           forKey:download];
        complete(temp);
    };
    if (self.testRoot) {
        start([self.testRoot URLByAppendingPathComponent:@"Downloaded model box.nc"]);
        return;
    }
    NSSavePanel *panel = [NSSavePanel savePanel];
    panel.nameFieldStringValue = filename.lastPathComponent.length ? filename.lastPathComponent : @"BalloonPredictor export";
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse response) {
        start(response == NSModalResponseOK ? panel.URL : nil);
    }];
}

- (void)downloadDidFinish:(WKDownload *)download {
    NSDictionary *entry = [self.downloads objectForKey:download];
    [self.downloads removeObjectForKey:download];
    if (!entry) return;
    NSFileManager *files = [NSFileManager defaultManager];
    NSURL *destination = entry[@"destination"];
    NSURL *temp = entry[@"temp"];
    NSError *error = nil;
    BOOL ok = [entry[@"replace"] boolValue]
        ? [files replaceItemAtURL:destination withItemAtURL:temp backupItemName:nil
                         options:NSFileManagerItemReplacementUsingNewMetadataOnly resultingItemURL:NULL error:&error]
        : [files moveItemAtURL:temp toURL:destination error:&error];
    if (!ok) {
        [files removeItemAtURL:temp error:NULL];
        if (self.testRoot) [self fail:error.localizedDescription];
        else [self showAlert:@"Could not save the download" detail:error.localizedDescription];
        return;
    }
    if (!self.testRoot) return;

    NSFileHandle *result = [NSFileHandle fileHandleForReadingFromURL:destination error:&error];
    NSData *header = [result readDataOfLength:8];
    [result closeFile];
    NSData *netcdf = [NSData dataWithBytes:"\211HDF\r\n\032\n" length:8];
    if (![header isEqualToData:netcdf]) {
        [self fail:@"WebKit did not save the expected NetCDF file."];
        return;
    }
    NSDictionary *report = @{
        @"architecture": nativeArch, @"python": self.engineReport[@"python"],
        @"macos": NSProcessInfo.processInfo.operatingSystemVersionString,
        @"engine": @"passed: GRIB, GeoTIFF, flight and both NetCDF exports",
        @"window": @"passed: a Demo flight calculated in WebKit",
        @"download": @"passed: WebKit saved a model box NetCDF"
    };
    NSData *json = [NSJSONSerialization dataWithJSONObject:report options:NSJSONWritingPrettyPrinted error:&error];
    if (!json || ![json writeToURL:[self.testRoot URLByAppendingPathComponent:@"smoke-result.json"]
                           options:NSDataWritingAtomic error:&error]) {
        [self fail:error.localizedDescription];
        return;
    }
    self.quitting = YES;
    [NSApp terminate:nil];
}

- (void)download:(WKDownload *)download didFailWithError:(NSError *)error resumeData:(NSData *)resumeData {
    NSURL *temp = [self.downloads objectForKey:download][@"temp"];
    if (temp) [[NSFileManager defaultManager] removeItemAtURL:temp error:NULL];
    [self.downloads removeObjectForKey:download];
    if (self.testRoot) [self fail:error.localizedDescription];
    else if (error.code != NSURLErrorCancelled) [self showAlert:@"Could not download the file" detail:error.localizedDescription];
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender {
    return YES;
}

- (void)applicationWillTerminate:(NSNotification *)notification {
    self.quitting = YES;
    [self.readyTimer invalidate];
    [self.testTimer invalidate];
    [self.shutdownPipe closeFile];
    if (self.sessionDir) [[NSFileManager defaultManager] removeItemAtURL:self.sessionDir error:NULL];
    for (WKDownload *download in self.downloads) {
        NSURL *temp = [self.downloads objectForKey:download][@"temp"];
        if (temp) [[NSFileManager defaultManager] removeItemAtURL:temp error:NULL];
    }
}
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *app = NSApplication.sharedApplication;
        [app setActivationPolicy:NSApplicationActivationPolicyRegular];
        __attribute__((objc_precise_lifetime)) BalloonApp *delegate = [BalloonApp new];
        NSArray<NSString *> *args = NSProcessInfo.processInfo.arguments;
        NSUInteger flag = [args indexOfObject:@"--smoke-test"];
        if (flag != NSNotFound) {
            if (flag + 1 >= args.count || ![args[flag + 1] isAbsolutePath]) return 2;
            delegate.testRoot = [NSURL fileURLWithPath:args[flag + 1] isDirectory:YES];
        }
        app.delegate = delegate;
        [app run];
    }
    return 0;
}
