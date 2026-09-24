import z from 'schemastery';
export declare const name = "@dsh-external/dsh-blender-plugin";
export declare const inject: string[];
export interface Config {
    port: number;
    autoStart: boolean;
}
export declare const Config: z<Schemastery.ObjectS<NoInfer<{
    port: z<number, number, "defined">;
    autoStart: z<boolean, boolean, "defined">;
}>>, Schemastery.ObjectT<NoInfer<{
    port: z<number, number, "defined">;
    autoStart: z<boolean, boolean, "defined">;
}>>, "plain">;
export declare function apply(ctx: any, config: Config): void;
